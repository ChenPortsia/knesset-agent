"""search_protocols: find what was said in committee.

Searches the protocol chunks that knesset_data publishes (the `rag` schema): runs of
consecutive speaker turns of about 200 words, each with its committee, date and speakers.
Two rankings are merged: by keywords (BM25) and by meaning (embeddings). How much more a
keyword match counts depends on the embedding model and is stored with the vectors
(rag.build.keyword_weight): 1 for a model that is good at meaning, more for a weak one.
Keyword search only matches Hebrew words, so queries should be in Hebrew.
Filters narrow the chunks before ranking.
"""

from collections.abc import Callable
from datetime import date
from functools import cache
from typing import Any

import duckdb

MAX_RESULTS = 10

Embed = Callable[[duckdb.DuckDBPyConnection, str], list[float]]

SEARCH = """
    WITH chosen AS (
        SELECT chunks.*, committees.committee_name
        FROM rag.chunks AS chunks
        INNER JOIN gold.dim_committee AS committees
            ON chunks.committee_id = committees.committee_id
        WHERE ($category_id IS NULL OR committees.category_id = $category_id)
          AND ($person_id IS NULL OR LIST_CONTAINS(chunks.person_ids, $person_id))
          AND ($date_from IS NULL OR chunks.session_date >= $date_from)
          AND ($date_to IS NULL OR chunks.session_date <= $date_to)
    ),

    by_keywords AS (
        SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY score DESC) AS position
        FROM (
            SELECT chunk_id, fts_rag_chunks.match_bm25(chunk_id, $query) AS score FROM chosen
        )
        WHERE score IS NOT NULL
        QUALIFY position <= 50
    ),

    by_meaning AS (
        SELECT
            chosen.chunk_id,
            ROW_NUMBER() OVER (
                ORDER BY ARRAY_COSINE_SIMILARITY(
                    embeddings.embedding, CAST($vector AS FLOAT[{size}])
                ) DESC
            ) AS position
        FROM chosen
        INNER JOIN rag.embeddings AS embeddings USING (chunk_id)
        QUALIFY position <= 50
    ),

    scored AS (
        -- reciprocal rank fusion: high in either ranking is good, high in both is best
        SELECT
            chunk_id,
            COALESCE({keyword_weight} / (60 + by_keywords.position), 0)
            + COALESCE(1 / (60 + by_meaning.position), 0) AS score
        FROM by_keywords
        FULL JOIN by_meaning USING (chunk_id)
    )

    SELECT
        chosen.chunk_id, chosen.committee_session_id, chosen.first_turn, chosen.last_turn,
        chosen.committee_name,
        CAST(chosen.session_date AS VARCHAR) AS session_date, chosen.speakers,
        chosen.chunk_text
    FROM scored
    INNER JOIN chosen USING (chunk_id)
    ORDER BY scored.score DESC, chosen.chunk_id
    LIMIT $limit
"""


@cache
def _model(name: str) -> Any:
    from fastembed import TextEmbedding  # here, not at the top: slow to import and load

    return TextEmbedding(name)


def embed(con: duckdb.DuckDBPyConnection, query: str) -> list[float]:
    """The query's embedding, from the model the chunks were embedded with."""
    name, prefix = con.execute("SELECT embedding_model, question_prefix FROM rag.build").fetchone()
    return next(iter(_model(name).embed([prefix + query]))).tolist()


def search_protocols(
    con: duckdb.DuckDBPyConnection,
    query: str,
    committee_category_id: int | None = None,
    person_id: int | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    limit: int = 6,
    embed: Embed = embed,
) -> list[dict[str, Any]]:
    """The chunks of committee protocols that best match a query, best first.

    Filters: a committee across Knessets (dim_committee.category_id), an MK who speaks in
    the chunk (dim_person.person_id), and a range of session dates.
    """
    vector = embed(con, query)
    keyword_weight = con.execute("SELECT keyword_weight FROM rag.build").fetchone()[0]
    cursor = con.execute(
        SEARCH.format(size=len(vector), keyword_weight=keyword_weight),
        {
            "query": query, "vector": vector, "category_id": committee_category_id,
            "person_id": person_id, "date_from": date_from, "date_to": date_to,
            "limit": max(1, min(limit, MAX_RESULTS)),
        },
    )  # fmt: skip
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
