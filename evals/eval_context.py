"""Does get_context find what each question needs? (Milestone 2's "done when".)

    python -m evals.eval_context          # summary and misses
    python -m evals.eval_context -v       # also what each question got

For every question in question_bank.yaml, the ids in `needs` must be among the items
get_context returns. Exits with 1 if any is missing.
"""

import sys
from pathlib import Path

import yaml

from tools.context import connect, get_context

BANK = Path(__file__).parent / "question_bank.yaml"


def main() -> int:
    verbose = "-v" in sys.argv
    bank = yaml.safe_load(BANK.read_text(encoding="utf-8"))
    con = connect()
    known = {r[0] for r in con.execute("SELECT id FROM context.items").fetchall()}
    unknown = sorted({i for q in bank for i in q["needs"]} - known)
    if unknown:
        print(f"question_bank.yaml needs items that don't exist: {unknown}")
        return 1

    needed = found = returned = failed = 0
    for entry in bank:
        got = [item["id"] for item in get_context(entry["question"], con)]
        missing = [i for i in entry["needs"] if i not in got]
        needed += len(entry["needs"])
        found += len(entry["needs"]) - len(missing)
        returned += len(got)
        failed += bool(missing)
        if missing or verbose:
            print(f"{'MISS' if missing else 'ok  '} {entry['question']}")
        if missing:
            print(f"       missing: {missing}")
        if verbose:
            print(f"       got: {got}")
    print(
        f"\n{len(bank) - failed} of {len(bank)} questions got everything they need; "
        f"{found} of {needed} needed items found ({found / needed:.0%}); "
        f"{returned / len(bank):.0f} items returned per question"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
