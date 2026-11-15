"""Where agent runs are recorded: our own tables, and a Langfuse trace.

ops.agent_runs    one row per question: ids, model, answer, tokens, time
ops.agent_steps   one row per tool call of a run: tool, input, output

They live in data/agent_ops.duckdb in this repo (Postgres at the pilot). `trace_id` is the same id
in Langfuse, so a row here opens as a full trace there.
"""

import json
import os
import threading
from pathlib import Path
from typing import Any

import duckdb

OPS = Path(
    os.environ.get("KNESSET_OPS", Path(__file__).resolve().parents[1] / "data/agent_ops.duckdb")
)
RUN_COLUMNS = (
    "run_id", "trace_id", "session_id", "user_id", "data_version", "model", "question",
    "kind", "text", "sql", "context_ids", "input_tokens", "output_tokens", "seconds",
)  # fmt: skip
DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.agent_runs (
    run_id VARCHAR PRIMARY KEY, trace_id VARCHAR, session_id VARCHAR, user_id VARCHAR,
    data_version VARCHAR, model VARCHAR, question VARCHAR,
    kind VARCHAR, text VARCHAR, sql VARCHAR, context_ids VARCHAR[],
    input_tokens BIGINT, output_tokens BIGINT, seconds DOUBLE,
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ops.agent_steps (
    run_id VARCHAR, step INTEGER, tool VARCHAR, input JSON, output VARCHAR
);
"""
_lock = threading.Lock()  # one writer at a time, also when the test runs questions side by side


def tracing(run: dict[str, Any]) -> tuple[list[Any], str | None]:
    """Langfuse callbacks and the trace id for a run; nothing when Langfuse isn't configured."""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return [], None
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    trace_id = Langfuse.create_trace_id(seed=run["run_id"])
    return [CallbackHandler(trace_context={"trace_id": trace_id})], trace_id


def save(run: dict[str, Any], steps: list[dict[str, Any]], ops: Path = OPS) -> None:
    ops.parent.mkdir(parents=True, exist_ok=True)
    with _lock, duckdb.connect(str(ops)) as con:
        con.execute(DDL)
        con.execute(
            f"INSERT INTO ops.agent_runs ({', '.join(RUN_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(RUN_COLUMNS))})",
            [run.get(column) for column in RUN_COLUMNS],
        )
        con.executemany(
            "INSERT INTO ops.agent_steps VALUES (?, ?, ?, ?, ?)",
            [
                (run["run_id"], i + 1, s["tool"], json.dumps(s["input"], ensure_ascii=False),
                 s["output"][:10_000])
                for i, s in enumerate(steps)
            ] or [(run["run_id"], 0, None, None, None)],
        )  # fmt: skip
