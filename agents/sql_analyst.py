"""The SQL analyst: one agent that answers structured questions from the gold tables.

    python -m agents.sql_analyst "כמה חוקים עברו בכנסת ה-25?"      (make ask Q="...")

It works in a loop: the model reads the question and its context, calls a tool, reads the
result, and repeats until it can answer. The context for the question is fetched by code and
put in the first message, so the agent never works without it.
"""

import json
import logging
import os
import sys
import time
import uuid
from datetime import date
from typing import Any, Literal

import duckdb
import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ops import runlog
from tools import context, odata, sql
from tools.db import connect, data_version
from tools.model import load_model

load_dotenv()
logging.getLogger("google_genai.types").setLevel(logging.ERROR)  # noise about tool-call parts

MAX_STEPS = 25  # model turns and tool calls together; a run that needs more is stuck

PROMPT = """\
You are a data analyst for the Israeli Knesset. You answer questions from a database of
parliamentary activity since 2015: bills, plenum votes, committees, parliamentary queries,
members, factions and governments.

How to work:
- The user's message starts with context chosen for the question: tables with their columns,
  terms, rules, metrics and verified queries. Trust it over your own knowledge of the Knesset.
  Follow every rule in it. If a verified query fits the question, adapt it.
- Use run_sql to get the facts. Never answer with a number you did not get from a query in
  this conversation.
- Names in the data are in Hebrew. An alias in the context gives exact ids: use them. For any
  other name or code, check its spelling with sample_values before filtering on it.
- If the context lacks something, call get_context with a more specific question.
- If a query fails, read the error, fix the query and try again.
- The database is loaded once a day; the user's message says how far it runs. Only when the
  question is about something after that date, use odata_query (the live Knesset API), and
  say in the answer that it came from the live API. For everything else use run_sql.

How to answer:
- kind "answer": the question is answered from query results. Give the numbers and say which
  Knesset or dates they cover. Mention a caveat only when a rule in the context says it matters.
- kind "clarify": the question can be read in clearly different ways (which vote? active by
  what measure?) and the readings would give different answers. Ask one short question. Do not
  guess, and do not run queries first.
- kind "out_of_scope": the database cannot answer (before 2015, opinions, what was said in
  debates, events outside the Knesset). Say what is missing.
- Write the answer in the language of the question, briefly, with no SQL in the text.
"""


class Answer(BaseModel):
    """The agent's final answer, with its evidence."""

    kind: Literal["answer", "clarify", "out_of_scope"]
    text: str = Field(description="The answer, in the language of the question")
    sql: str | None = Field(default=None, description="The query whose result the answer uses")
    context_ids: list[str] = Field(default=[], description="Ids of the context items used")


def make_tools(con: duckdb.DuckDBPyConnection) -> list[Any]:
    """The agent's tools. Each call gets its own cursor, so tools can run side by side."""

    @tool
    def get_context(question: str) -> str:
        """Search the context layer again, for tables, terms, rules or example queries that the
        first context did not include. Ask a specific question."""
        return context.render(context.get_context(question, con.cursor()))

    @tool
    def run_sql(query: str) -> str:
        """Run one SELECT on the gold tables (DuckDB SQL; table names without a schema).
        Returns at most 200 rows as JSON. Aggregate in SQL rather than reading many rows."""
        try:
            return json.dumps(sql.run_sql(con, query), ensure_ascii=False, default=str)
        except (sql.Refused, duckdb.Error) as error:
            return f"ERROR: {error}"

    @tool
    def sample_values(column: str, like: str | None = None) -> str:
        """The most common values of a column, given as table.column, with their row counts.
        `like` keeps values containing that text. Use it to check how a name or code is
        written before filtering on it."""
        try:
            values = sql.sample_values(con.cursor(), column, like)
            return json.dumps(values, ensure_ascii=False, default=str)
        except (sql.Refused, duckdb.Error) as error:
            return f"ERROR: {error}"

    @tool
    def odata_query(
        table: str, filter: str | None = None, orderby: str | None = None, top: int = 20
    ) -> str:
        """Read up to 50 rows from the live Knesset API, for facts newer than the data.
        Tables: KNS_Bill, KNS_PlenumVote, KNS_PlenumSession, KNS_CommitteeSession,
        KNS_Committee, KNS_Query, KNS_Agenda, KNS_Person, KNS_PersonToPosition, KNS_Faction,
        KNS_IsraelLaw. `filter` and `orderby` are OData expressions on the API's own column
        names (see odata_fields), e.g. filter="KnessetNum eq 26", orderby="Id desc"."""
        try:
            rows = odata.odata_query(table, filter, orderby, top=top)
            return json.dumps(rows, ensure_ascii=False, default=str)
        except (odata.Refused, httpx.HTTPError) as error:
            return f"ERROR: {error}"

    @tool
    def odata_fields(table: str) -> str:
        """The column names of a live API table. Call it before filtering with odata_query."""
        try:
            return json.dumps(odata.odata_fields(table))
        except (odata.Refused, httpx.HTTPError) as error:
            return f"ERROR: {error}"

    return [get_context, run_sql, sample_values, odata_query, odata_fields]


def first_message(con: duckdb.DuckDBPyConnection, question: str) -> str:
    newest_vote, newest_session = con.execute(
        "SELECT newest_vote_date, newest_session_date FROM build_info"
    ).fetchone()
    found = context.render(context.get_context(question, con.cursor()))
    return (
        f"<context>\n{found}\n</context>\n\n"
        f"Today is {date.today()}. The data runs to {max(newest_vote, newest_session)}.\n\n"
        f"Question: {question}"
    )


def steps_of(messages: list[Any]) -> list[dict[str, Any]]:
    """Each tool call of a run with its result, in order."""
    results = {m.tool_call_id: m.content for m in messages if m.type == "tool"}
    return [
        {"tool": call["name"], "input": call["args"], "output": str(results.get(call["id"], ""))}
        for m in messages
        if m.type == "ai"
        for call in m.tool_calls
    ]


def ask(
    question: str,
    con: duckdb.DuckDBPyConnection | None = None,
    model: str | None = None,
    session_id: str | None = None,
    user_id: str = "local",
) -> dict[str, Any]:
    """Answer one question. Returns the answer with its run record (also logged to ops)."""
    con = con or connect()
    model = model or os.environ["KNESSET_MODEL"]
    run = {
        "run_id": str(uuid.uuid4()),
        "session_id": session_id or str(uuid.uuid4()),
        "user_id": user_id,
        "data_version": data_version(con),
        "model": model,
        "question": question,
    }
    callbacks, run["trace_id"] = runlog.tracing(run)
    agent = create_agent(
        load_model(model), make_tools(con), system_prompt=PROMPT, response_format=Answer
    )
    started = time.time()
    steps: list[dict[str, Any]] = []
    try:
        state = agent.invoke(
            {"messages": [{"role": "user", "content": first_message(con, question)}]},
            config={
                "callbacks": callbacks,
                "recursion_limit": MAX_STEPS,
                "metadata": {
                    "langfuse_session_id": run["session_id"],
                    "langfuse_user_id": user_id,
                },
            },
        )
        answer: Answer = state["structured_response"]
        steps = steps_of(state["messages"])
        usage = [m.usage_metadata or {} for m in state["messages"] if m.type == "ai"]
        served = [
            m.response_metadata.get("model_name") for m in state["messages"] if m.type == "ai"
        ]
        run |= answer.model_dump() | {
            "model_served": next((name for name in reversed(served) if name), None),
            "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
            "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
        }
    except Exception as error:  # a failed run is still a run: log it, then report it
        run |= {"kind": "error", "text": f"{type(error).__name__}: {error}"}
    run["seconds"] = round(time.time() - started, 1)
    runlog.save(run, steps)
    return run | {"steps": steps}


if __name__ == "__main__":
    result = ask(sys.argv[1])
    print(f"\n{result['text']}\n")
    if result.get("sql"):
        print(f"SQL:\n{result['sql']}\n")
    print(
        f"[{result['kind']} | {len(result['steps'])} tool calls | {result['seconds']}s | "
        f"{result.get('input_tokens', 0)} + {result.get('output_tokens', 0)} tokens | "
        f"data {result['data_version']} | trace {result['trace_id']}]"
    )
