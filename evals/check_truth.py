"""Does every ground-truth query in answer_bank.yaml run and return rows?

    python -m evals.check_truth       (make eval-truth; V=1 to see each result)

Queries run through run_sql, so ground truth obeys the same limits as the agent.
"""

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import yaml

from tools.db import connect
from tools.sql import run_sql

BANK = Path(__file__).parent / "answer_bank.yaml"


def truth(con: duckdb.DuckDBPyConnection, entry: dict[str, Any]) -> dict[str, Any] | None:
    """The true result of a question, or None when the right answer isn't data."""
    if "vq" in entry:
        row = con.execute(
            "SELECT payload ->> 'sql' FROM context.items WHERE id = ?", [entry["vq"]]
        ).fetchone()
        if row is None:
            raise LookupError(f"{entry['vq']} is not a verified query")
        return run_sql(con, row[0].replace('"warehouse".', ""))
    return run_sql(con, entry["sql"]) if "sql" in entry else None


def main() -> int:
    verbose = "-v" in sys.argv
    bank = yaml.safe_load(BANK.read_text(encoding="utf-8"))
    con = connect()
    problems = 0
    for entry in bank:
        try:
            result = truth(con, entry)
            problem = None
            if result is None and entry.get("expect") not in ("clarify", "out_of_scope"):
                problem = "needs sql, vq or expect"
            elif result is not None and not result["rows"]:
                problem = "returned no rows"
        except Exception as error:
            result, problem = None, str(error).splitlines()[0]
        problems += bool(problem)
        if problem or verbose:
            print(f"{'BAD ' if problem else 'ok  '} {entry['question']}")
        if problem:
            print(f"       {problem}")
        elif verbose and result:
            print(f"       {result['columns']}")
            for row in result["rows"][:5]:
                print(f"       {json.dumps(row, ensure_ascii=False, default=str)}")
    held = sum(bool(e.get("holdout")) for e in bank)
    print(f"\n{len(bank) - problems} of {len(bank)} ground truths ok; {held} held out")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
