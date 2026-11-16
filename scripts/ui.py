"""Open DuckDB's UI in the browser with the agent's tables and the data, all read-only.

    make ui

    agent_ops   ops.agent_runs, ops.agent_steps, ops.agent_queries   (this repo)
    knesset     the published database the agent answers from: gold, context
    warehouse, raw   knesset_data's own files, when they are next to it
    eval_results     (a view) every graded answer of every saved test run

DuckDB allows one writer or many readers per file, so close this before asking the agent,
running the test, or make ingest / make dbt in knesset_data.
"""

from pathlib import Path

import duckdb

from ops.runlog import OPS
from tools.db import DATABASE

DATA = DATABASE.parents[1]  # knesset_data/data
FILES = {
    "agent_ops": OPS,
    "knesset": DATABASE,
    "warehouse": DATA / "warehouse" / "warehouse.duckdb",
    "raw": DATA / "raw" / "raw.duckdb",
}
RUNS = Path(__file__).resolve().parents[1] / "data" / "eval_runs"

con = duckdb.connect()
for name, path in FILES.items():
    if path.exists():
        con.execute(f"ATTACH '{path}' AS {name} (READ_ONLY)")
        print(f"attached {name}")
if any(RUNS.glob("*.json")):
    con.execute(
        f"""
        CREATE VIEW eval_results AS
        SELECT REGEXP_EXTRACT(filename, '([0-9_]+)\\.json$', 1) AS run, * EXCLUDE (filename)
        FROM READ_JSON('{RUNS}/*.json', filename = true, union_by_name = true)
        """
    )
    print("created view eval_results")
con.execute("CALL start_ui()")
input("The UI is open at http://localhost:4213 . Press Enter here to close it.\n")
