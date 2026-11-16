"""The text test: does search find the passage, and does the answer stick to it?

    python -m evals.eval_text --retrieval-only    search only: no model, no cost
    python -m evals.eval_text                     search, answer, grade   (make eval-text)
    python -m evals.eval_text --limit 5

The same three questions RAGAS asks, measured simply:
    1. Was the right passage found?      Is the labelled passage among the top results, and
                                         how high (hit at 1, 3 and 6; mean reciprocal rank).
    2. Is the answer correct?            A grader compares it with the reference answer.
    3. Is the answer faithful?           The grader checks every claim is in the passages.
Answering is one model call over the top passages and grading is one more, so a full run is
about two short calls per question. (This tests search and answer, not the agent's loop.)

The protocols are in Hebrew and keyword search matches words, so a question in another
language is first turned into a Hebrew search query by the model, as the agent does itself.
With --retrieval-only there is no model, so those questions are searched as they are.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from tools.db import connect
from tools.model import load_model
from tools.search import search_protocols

load_dotenv()
BANK = Path(__file__).parent / "text_bank.yaml"
RUNS = Path(__file__).resolve().parents[1] / "data" / "eval_runs"
TOP = 6  # passages searched for and given to the model

ANSWER = """\
Answer the question from the passages of Knesset committee protocols below, and only from
them. Answer in the language of the question, in one to three sentences. If the passages do
not contain the answer, say that they don't.

Question: {question}

Passages:
{passages}
"""
GRADE = """\
You grade an answer to a question about what was said in a Knesset committee.

correct: the answer gives the same substance as the reference answer. Wording and language
may differ and extra detail is fine; a missing or different key fact is not.
faithful: every claim in the answer is supported by the passages. An answer that says the
passages don't contain the answer is faithful.

Question: {question}

Reference answer: {reference}

Passages the answer was written from:
{passages}

Answer to grade: {answer}
"""


TO_HEBREW = """\
Write a short Hebrew search query for finding the answer to this question in the protocols
of Knesset committees: the key names and terms, as they would be said in Hebrew. Reply with
the query only.

Question: {question}
"""


def search_query(question: str, use_model: bool) -> str:
    """The question itself if it is in Hebrew; otherwise a Hebrew query written by the model."""
    if re.search("[א-ת]", question) or not use_model:
        return question
    return load_model().invoke(TO_HEBREW.format(question=question)).text.strip()


class Grade(BaseModel):
    reason: str = Field(description="One sentence")
    correct: bool
    faithful: bool


def rank_of(entry: dict[str, Any], passages: list[dict[str, Any]]) -> int | None:
    """Position (from 1) of the first passage that covers the labelled turn."""
    for position, passage in enumerate(passages, start=1):
        if (
            passage["committee_session_id"] == entry["committee_session_id"]
            and passage["first_turn"] <= entry["turn"] <= passage["last_turn"]
        ):
            return position
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Test protocol search and answers")
    parser.add_argument("--retrieval-only", action="store_true", help="no model calls")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    args = parser.parse_args()

    bank = yaml.safe_load(BANK.read_text(encoding="utf-8"))[: args.limit]
    con = connect()
    results = []
    for entry in bank:
        query = search_query(entry["question"], use_model=not args.retrieval_only)
        passages = search_protocols(con, query, limit=TOP)
        result = entry | {"query": query, "rank": rank_of(entry, passages)}
        if not args.retrieval_only:
            shown = "\n\n".join(p["chunk_text"] for p in passages)
            answer = load_model().invoke(ANSWER.format(question=entry["question"], passages=shown))
            grade = (
                load_model(None)
                .with_structured_output(Grade)
                .invoke(
                    GRADE.format(
                        question=entry["question"], reference=entry["reference"],
                        passages=shown, answer=answer.text,
                    )
                )
            )  # fmt: skip
            result |= {"answer": answer.text} | grade.model_dump()
        results.append(result)
        marks = (
            ""
            if args.retrieval_only
            else f" correct={result['correct']!s:5} faithful={result['faithful']!s:5}"
        )
        print(f"rank {result['rank'] or '-':>2}{marks}  {entry['question'][:70]}", flush=True)

    n = len(results)
    ranks = [r["rank"] for r in results]
    print(f"\n{n} questions, top {TOP} passages")
    for k in (1, 3, TOP):
        hits = sum(rank is not None and rank <= k for rank in ranks)
        print(f"  right passage in the top {k}: {hits}/{n} ({hits / n:.0%})")
    print(f"  mean reciprocal rank: {sum(1 / rank for rank in ranks if rank) / n:.2f}")
    if not args.retrieval_only:
        for name in ("correct", "faithful"):
            count = sum(r[name] for r in results)
            print(f"  answers {name}: {count}/{n} ({count / n:.0%})")
        RUNS.mkdir(parents=True, exist_ok=True)
        path = RUNS / f"text_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
