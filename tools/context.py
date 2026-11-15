"""get_context: the few context items a question needs, out of hundreds.

Reads the `context` schema that knesset_data builds (`make context`) in warehouse.duckdb:
    context.items   every item: glossary, aliases, rules, verified queries, tables, columns, metrics
    context.search  each item's searchable text and embedding, with a keyword index
    context.build   the embedding model the index was built with

A question gets:
    1. every alias whose name appears in it (exact, so a name never resolves by guesswork)
    2. the rules marked `always`
    3. the best matches of each kind, by keywords and by meaning together
    4. what those matches point to: a glossary term's or a metric's tables, `see_also` items
"""

import json
import os
from collections import Counter
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import duckdb

WAREHOUSE = Path(
    os.environ.get(
        "KNESSET_WAREHOUSE",
        Path(__file__).resolve().parents[2] / "knesset_data/data/warehouse/warehouse.duckdb",
    )
)
# How many searched items of each kind a question gets, best first
QUOTAS = {"table": 4, "glossary": 4, "rule": 3, "metric": 3, "verified_query": 3}

Embed = Callable[[duckdb.DuckDBPyConnection, str], list[float]]

ALIASES_NAMED = """
    SELECT DISTINCT items.id
    FROM context.items, UNNEST(CAST(payload -> 'names' AS VARCHAR[])) AS names (name)
    WHERE kind = 'alias' AND CONTAINS(LOWER($question), LOWER(name))
"""
RULES_ALWAYS = """
    SELECT id FROM context.items WHERE kind = 'rule' AND CAST(payload -> 'always' AS BOOLEAN)
"""
# Two rankings, by keywords (BM25) and by meaning (embedding similarity), merged with
# reciprocal rank fusion: an item's score is 1 / (60 + its position) in each ranking, added.
# An item high in either ranking scores well; an item high in both scores best.
SEARCH = """
    WITH by_keywords AS (
        SELECT id, ROW_NUMBER() OVER (ORDER BY score DESC) AS position
        FROM (
            SELECT id, fts_context_search.match_bm25(id, $question) AS score FROM context.search
        )
        WHERE score IS NOT NULL
    ),

    by_meaning AS (
        SELECT
            id,
            ROW_NUMBER() OVER (
                ORDER BY ARRAY_COSINE_SIMILARITY(embedding, CAST($vector AS FLOAT[{size}])) DESC
            ) AS position
        FROM context.search
    ),

    scored AS (
        SELECT
            id,
            1 / (60 + by_meaning.position) + COALESCE(1 / (60 + by_keywords.position), 0) AS score
        FROM by_meaning
        LEFT JOIN by_keywords USING (id)
    )

    -- A column counts for its table: the agent needs whole tables, not single columns
    SELECT
        IF(kind = 'column', 'table.' || (payload ->> 'model'), id) AS found_id,
        IF(kind = 'column', 'table', kind) AS found_kind,
        MAX(score) AS score
    FROM scored
    INNER JOIN context.items USING (id)
    WHERE kind <> 'alias'
    GROUP BY ALL
    ORDER BY score DESC
"""
ITEMS = """
    SELECT id, kind, title, text, text_he, payload FROM context.items WHERE id IN $ids
"""
COLUMNS = """
    SELECT
        payload ->> 'model' AS model,
        SPLIT_PART(title, '.', 2) AS name,
        text,
        text_he,
        payload ->> 'data_type' AS data_type
    FROM context.items
    WHERE kind = 'column' AND (payload ->> 'model') IN $models
    ORDER BY id
"""


def connect() -> duckdb.DuckDBPyConnection:
    """The warehouse, read-only, with keyword search loaded."""
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.execute("INSTALL fts; LOAD fts")
    return con


@cache
def _model(name: str) -> Any:
    from fastembed import TextEmbedding  # here, not at the top: slow to import and load

    return TextEmbedding(name)


def embed(con: duckdb.DuckDBPyConnection, question: str) -> list[float]:
    """The question's embedding, from the model the index was built with."""
    name, prefix = con.execute(
        "SELECT embedding_model, question_prefix FROM context.build"
    ).fetchone()
    return next(iter(_model(name).embed([prefix + question]))).tolist()


def search(con: duckdb.DuckDBPyConnection, question: str, embed: Embed = embed) -> list[str]:
    """Ids of the best matches, at most QUOTAS[kind] of each kind, best first."""
    vector = embed(con, question)
    rows = con.execute(
        SEARCH.format(size=len(vector)), {"question": question, "vector": vector}
    ).fetchall()
    ids, taken = [], Counter()
    for id_, kind, _ in rows:
        if taken[kind] < QUOTAS.get(kind, 0):
            taken[kind] += 1
            ids.append(id_)
    return ids


def load(con: duckdb.DuckDBPyConnection, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Items by id; each table with all its columns."""
    items = {}
    for id_, kind, title, text, text_he, payload in con.execute(ITEMS, {"ids": ids}).fetchall():
        items[id_] = {
            "id": id_, "kind": kind, "title": title, "text": text, "text_he": text_he,
            "payload": json.loads(payload),
        }  # fmt: skip
    tables = [item for item in items.values() if item["kind"] == "table"]
    for table in tables:
        table["payload"]["columns"] = []
    models = [table["title"] for table in tables]
    for model, name, text, text_he, type_ in con.execute(COLUMNS, {"models": models}).fetchall():
        items[f"table.{model}"]["payload"]["columns"].append(
            {"name": name, "type": type_, "text": text, "text_he": text_he}
        )
    return items


def linked(item: dict[str, Any]) -> list[str]:
    """Ids an item points to: a glossary term's or a metric's tables, and see_also items."""
    payload = item["payload"]
    tables = [m["column"].split(".")[0] for m in payload.get("maps_to", [])]
    tables += payload.get("tables", [])
    return [f"table.{table}" for table in tables] + payload.get("see_also", [])


def get_context(
    question: str, con: duckdb.DuckDBPyConnection | None = None, embed: Embed = embed
) -> list[dict[str, Any]]:
    """The context items for a question, most certain first.

    Each item has id, kind, title, text, text_he and payload (the whole item). A table's
    payload carries all its columns with their descriptions and types.
    """
    con = con or connect()
    named = [r[0] for r in con.execute(ALIASES_NAMED, {"question": question}).fetchall()]
    always = [r[0] for r in con.execute(RULES_ALWAYS).fetchall()]
    searched = search(con, question, embed)
    links = [id_ for item in load(con, searched).values() for id_ in linked(item)]
    ids = list(dict.fromkeys(named + always + searched + links))  # in order, no repeats
    items = load(con, ids)
    return [items[id_] for id_ in ids if id_ in items]


if __name__ == "__main__":  # python -m tools.context "<question>"
    import sys

    for item in get_context(sys.argv[1]):
        print(f"{item['id']:50} {item['title']}")
