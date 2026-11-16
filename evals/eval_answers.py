"""The answer test: does the agent answer the questions in answer_bank.yaml correctly?

    python -m evals.eval_answers              (make eval-answers)
    python -m evals.eval_answers --limit 5    a quick try on the first questions
    python -m evals.eval_answers --workers 1  one at a time, for a rate-limited key
    python -m evals.eval_answers --failed     only the questions the last run got wrong
    python -m evals.eval_answers --category votes

A full run is about 130 model calls for the answers and 30 for the grading. To keep the cost
down, grade with a cheap model (KNESSET_JUDGE_MODEL) and rerun only what failed.

Each question is asked, then graded: against its ground-truth result by the judge, or, when
the right answer is a clarifying question or "the data can't say", by the answer's kind.
Failures of held-out questions are counted but not printed, so we can't tune to them.
The full results are saved to data/eval_runs/ for reading by hand.
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import yaml
from dotenv import load_dotenv

from agents.sql_analyst import ask
from evals.check_truth import BANK, truth
from evals.judge import judge
from tools.db import connect

load_dotenv()
RUNS = Path(__file__).resolve().parents[1] / "data" / "eval_runs"


def grade(con: duckdb.DuckDBPyConnection, entry: dict[str, Any]) -> dict[str, Any]:
    """Ask one question and grade the answer."""
    run = ask(entry["question"], con, user_id="eval")
    expected = entry.get("expect", "answer")
    if run["kind"] != expected:
        passed, reason = False, f"expected kind '{expected}', got '{run['kind']}'"
    elif expected != "answer":
        passed, reason = True, f"kind is '{expected}'"
    else:
        try:
            verdict = judge(entry["question"], truth(con, entry), run["text"])
            passed, reason = verdict.passed, verdict.reason
        except Exception as error:
            passed, reason = False, f"judge failed: {type(error).__name__}: {error}"
    # Progress as each question finishes; a held-out question doesn't show how it went
    status = "held" if entry.get("holdout") else "pass" if passed else "FAIL"
    print(f"{status} {run['seconds']:>5}s  {entry['question'][:70]}", flush=True)
    return entry | {
        "passed": passed, "reason": reason, "kind": run["kind"], "answer": run["text"],
        "sql": run.get("sql"), "model_calls": run.get("model_calls", 0),
        "seconds": run["seconds"],
        "tokens": run.get("input_tokens", 0) + run.get("output_tokens", 0),
        "trace_id": run["trace_id"],
    }  # fmt: skip


def share(results: list[dict[str, Any]]) -> str:
    passed = sum(r["passed"] for r in results)
    return f"{passed}/{len(results)} ({passed / len(results):.0%})" if results else "-"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and grade the answer bank")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument("--workers", type=int, default=3, help="questions asked side by side")
    parser.add_argument("--category", help="only this category, e.g. votes")
    parser.add_argument("--failed", action="store_true", help="only what the last run got wrong")
    args = parser.parse_args()

    bank = yaml.safe_load(BANK.read_text(encoding="utf-8"))
    if args.category:
        bank = [e for e in bank if e["category"] == args.category]
    if args.failed:
        last = json.loads(sorted(RUNS.glob("*.json"))[-1].read_text())
        wrong = {r["question"] for r in last if not r["passed"]}
        bank = [e for e in bank if e["question"] in wrong]
    bank = bank[: args.limit]
    if not bank:
        print("no questions to run")
        return 0
    con = connect()  # one connection; each question works on its own cursor
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda entry: grade(con.cursor(), entry), bank))

    errors = [r for r in results if r["kind"] == "error"]
    if errors:  # e.g. a rate limit: the run says nothing about the agent
        print(
            f"{len(errors)} of {len(results)} questions ended in an error, so this is not a score."
        )
        print(f"first error: {errors[0]['answer'][:300]}")
        return 2
    for r in results:
        if not r["passed"] and not r.get("holdout"):
            print(f"FAIL [{r['category']}] {r['question']}\n       {r['reason']}")
            print(f"       answer: {' '.join(r['answer'].split())[:300]}")
    print(f"\nmodel: {os.environ['KNESSET_MODEL']}")
    for category in dict.fromkeys(r["category"] for r in results):
        print(f"  {category:13} {share([r for r in results if r['category'] == category])}")
    tuned = [r for r in results if not r.get("holdout")]
    held = [r for r in results if r.get("holdout")]
    seconds = sum(r["seconds"] for r in results) / len(results)
    tokens = sum(r["tokens"] for r in results)
    print(f"\ntuned on {share(tuned)}   held out {share(held)}   all {share(results)}")
    calls = sum(r["model_calls"] for r in results)
    print(
        f"{seconds:.0f}s, {calls / len(results):.1f} model calls and "
        f"{tokens // len(results):,} tokens per question; "
        f"{calls} calls and {tokens:,} tokens in all"
    )

    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"saved {path}")
    return 0 if sum(r["passed"] for r in results) / len(results) >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
