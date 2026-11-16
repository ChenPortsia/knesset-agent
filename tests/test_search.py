"""search_protocols on a tiny made-up rag schema (no model)."""

import duckdb
import pytest

from tools.search import search_protocols

# chunk_id, committee_id, date, person_ids, text, embedding
CHUNKS = [
    ("1-0-0", 10, "2024-02-13", [7], "ועדת הכספים · 2024-02-13\nגפני: מס על רווחי הבנקים", [1, 0]),
    ("1-5-0", 10, "2024-02-13", [8], "ועדת הכספים · 2024-02-13\nשירי: הגירעון גדל", [0, 1]),
    ("2-0-0", 20, "2023-06-01", [7], "ועדת הכלכלה · 2023-06-01\nגפני: מחירי הדלק", [0.9, 0.1]),
]


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute(
        """
        CREATE SCHEMA rag; CREATE SCHEMA gold;
        CREATE TABLE gold.dim_committee AS SELECT * FROM (VALUES
            (10, 'ועדת הכספים', 2), (20, 'ועדת הכלכלה', 3)) AS t (committee_id, committee_name,
            category_id);
        CREATE TABLE rag.chunks (chunk_id VARCHAR, committee_session_id INTEGER,
            committee_id INTEGER, session_date DATE, speakers VARCHAR[], person_ids INTEGER[],
            chunk_text VARCHAR);
        CREATE TABLE rag.embeddings (chunk_id VARCHAR, embedding FLOAT[2]);
        INSTALL fts; LOAD fts;
        """
    )
    for id_, committee, day, people, text, vector in CHUNKS:
        con.execute(
            "INSERT INTO rag.chunks VALUES (?, 1, ?, ?, ['x'], ?, ?)",
            [id_, committee, day, people, text],
        )
        con.execute("INSERT INTO rag.embeddings VALUES (?, ?)", [id_, vector])
    con.execute(
        "PRAGMA create_fts_index('rag.chunks', 'chunk_id', 'chunk_text', stemmer = 'none', "
        "stopwords = 'none', ignore = '[^a-z0-9א-ת]+')"
    )
    return con


def found(con, query: str, vector: list[float], **filters) -> list[str]:
    rows = search_protocols(con, query, embed=lambda *_: vector, **filters)
    return [row["chunk_id"] for row in rows]


def test_the_best_match_by_words_and_meaning_comes_first(con):
    assert found(con, "רווחי הבנקים", [1, 0])[0] == "1-0-0"


def test_meaning_alone_finds_a_passage_with_none_of_the_words(con):
    assert found(con, "deficit", [0, 1])[0] == "1-5-0"


def test_filters_narrow_before_ranking(con):
    assert found(con, "גפני", [1, 0], committee_category_id=3) == ["2-0-0"]
    assert set(found(con, "גפני", [1, 0], person_id=7)) == {"1-0-0", "2-0-0"}
    assert found(con, "גפני", [1, 0], date_from="2024-01-01", person_id=7) == ["1-0-0"]
    assert found(con, "גפני", [1, 0], date_to="2020-01-01") == []


def test_a_result_says_where_when_and_who(con):
    row = search_protocols(con, "הבנקים", embed=lambda *_: [1, 0])[0]
    assert row["committee_name"] == "ועדת הכספים" and row["session_date"] == "2024-02-13"
    assert "רווחי הבנקים" in row["chunk_text"]
