"""The agent's database: the file knesset_data publishes (`make publish`), opened read-only.

It holds only the gold tables, the context and `build_info`, so no query can reach silver or
bronze. Set KNESSET_DATABASE to use a file somewhere else.
"""

import os
from pathlib import Path

import duckdb

DATABASE = Path(
    os.environ.get(
        "KNESSET_DATABASE",
        Path(__file__).resolve().parents[2] / "knesset_data/data/published/knesset.duckdb",
    )
)


def connect(database: Path = DATABASE) -> duckdb.DuckDBPyConnection:
    """A read-only connection that can't read files or the network from SQL."""
    con = duckdb.connect(str(database), read_only=True)
    con.execute("INSTALL fts; LOAD fts")  # keyword search, used by get_context
    con.execute("SET enable_external_access = false; SET lock_configuration = true")
    return con


def data_version(con: duckdb.DuckDBPyConnection) -> str:
    """Which published build this is; stamped on every agent run."""
    return con.execute("SELECT data_version FROM build_info").fetchone()[0]
