"""run_sql and sample_values: how the agent reads the gold tables.

The limits are enforced here, in code; the prompt only explains them:
    - one SELECT statement, nothing else
    - at most MAX_ROWS rows come back
    - a query is stopped after TIMEOUT_SECONDS
The connection itself is read-only and can't read files or the network (tools/db.py).
"""

import re
import threading
from typing import Any

import duckdb

MAX_ROWS = 200
TIMEOUT_SECONDS = 30
COLUMN_REF = re.compile(r"^[a-z_0-9]+\.[a-z_0-9]+$")  # table.column


class Refused(ValueError):
    """The request breaks a rule; the message tells the agent which."""


def run_sql(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    """Run one SELECT on the gold schema. Returns columns, rows and whether rows were cut off."""
    statements = duckdb.extract_statements(sql)
    if len(statements) != 1:
        raise Refused("Send exactly one SQL statement.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise Refused("Only SELECT statements are allowed.")

    cursor = con.cursor()  # its own cursor, so stopping it doesn't touch other queries
    cursor.execute("USE gold")
    timer = threading.Timer(TIMEOUT_SECONDS, cursor.interrupt)
    timer.start()
    try:
        cursor.execute(statements[0])
        rows = cursor.fetchmany(MAX_ROWS + 1)
    except duckdb.InterruptException:
        raise Refused(
            f"The query ran longer than {TIMEOUT_SECONDS} seconds and was stopped. "
            "Filter or aggregate more."
        ) from None
    finally:
        timer.cancel()
    return {
        "columns": [column[0] for column in cursor.description],
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
    }


def sample_values(
    con: duckdb.DuckDBPyConnection, column: str, like: str | None = None, limit: int = 20
) -> list[tuple[Any, int]]:
    """The most common values of a gold column (table.column) with their counts.

    `like` keeps values containing that text, e.g. to check how a name is spelled.
    """
    if not COLUMN_REF.match(column):
        raise Refused("Give the column as table.column, e.g. dim_person.full_name.")
    table, name = column.split(".")
    known = con.execute(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'gold' AND table_name = ? AND column_name = ?
        """,
        [table, name],
    ).fetchone()[0]
    if not known:
        raise Refused(f"gold.{column} does not exist.")
    return con.execute(
        f"""
        SELECT {name} AS value, COUNT(*) AS rows
        FROM gold.{table}
        WHERE ? IS NULL OR CONTAINS(LOWER(CAST({name} AS VARCHAR)), LOWER(?))
        GROUP BY {name}
        ORDER BY rows DESC, value
        LIMIT ?
        """,
        [like, like, min(limit, MAX_ROWS)],
    ).fetchall()
