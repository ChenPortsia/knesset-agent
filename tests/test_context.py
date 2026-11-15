"""get_context on a tiny context, with made-up embeddings (no model, no warehouse)."""

import json

import duckdb
import pytest

from tools.context import QUOTAS, get_context

# id, kind, title, text_he, payload, embedding
ITEMS = [
    ("alias.committee.finance", "alias", "finance", "", {"names": ["ועדת הכספים"]}, [0, 0]),
    ("rule.gold_only", "rule", "Query gold only", "", {"always": True}, [0, 0]),
    ("rule.recess", "rule", "Recess", "פגרה", {"always": False}, [0, 1]),
    ("glossary.law", "glossary", "law", "חוק",
     {"maps_to": [{"column": "dim_person.person_id"}], "see_also": ["rule.recess"]}, [1, 0]),
    ("table.dim_person", "table", "dim_person", "אנשים", {"columns": []}, [0, 0]),
    ("table.dim_bill", "table", "dim_bill", "הצעות חוק", {"columns": ["bill_id"]}, [0, 0]),
    ("column.dim_bill.is_passed", "column", "dim_bill.is_passed", "התקבל כחוק",
     {"model": "dim_bill", "data_type": "boolean"}, [1, 0]),
    *[(f"glossary.other_{i}", "glossary", f"other {i}", "", {}, [0.9, 0.1]) for i in range(9)],
]  # fmt: skip


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute(
        """
        CREATE SCHEMA context;
        CREATE TABLE context.items (id VARCHAR, kind VARCHAR, title VARCHAR, text VARCHAR,
            text_he VARCHAR, payload JSON);
        CREATE TABLE context.search (id VARCHAR, search_text VARCHAR, embedding FLOAT[2]);
        INSTALL fts; LOAD fts;
        """
    )
    for id_, kind, title, text_he, payload, embedding in ITEMS:
        con.execute(
            "INSERT INTO context.items VALUES (?, ?, ?, ?, ?, ?)",
            [id_, kind, title, title, text_he, json.dumps(payload)],
        )
        con.execute(
            "INSERT INTO context.search VALUES (?, ?, ?)", [id_, f"{title} {text_he}", embedding]
        )
    con.execute(
        "PRAGMA create_fts_index('context.search', 'id', 'search_text', stemmer = 'none', "
        "stopwords = 'none', ignore = '[^a-z0-9א-ת]+')"
    )
    return con


def ids(con, question: str, vector: list[float]) -> list[str]:
    return [item["id"] for item in get_context(question, con, embed=lambda *_: vector)]


def test_an_alias_named_in_the_question_is_returned_even_with_a_hebrew_prefix(con):
    assert "alias.committee.finance" in ids(con, "כמה ישיבות היו בוועדת הכספים?", [0, 1])
    assert "alias.committee.finance" not in ids(con, "כמה ישיבות היו?", [0, 1])


def test_rules_marked_always_are_returned_for_every_question(con):
    assert "rule.gold_only" in ids(con, "anything", [0, 1])


def test_a_matching_column_brings_its_whole_table(con):
    items = get_context("is_passed", con, embed=lambda *_: [1, 0])
    table = next(item for item in items if item["id"] == "table.dim_bill")
    assert table["payload"]["columns"] == [
        {"name": "is_passed", "type": "boolean", "text": "dim_bill.is_passed",
         "text_he": "התקבל כחוק"}
    ]  # fmt: skip
    assert not any(item["kind"] == "column" for item in items)


def test_each_kind_is_limited_to_its_quota_best_first(con):
    found = ids(con, "law", [1, 0])
    glossary = [i for i in found if i.startswith("glossary.")]
    assert len(glossary) == QUOTAS["glossary"]
    assert glossary[0] == "glossary.law"  # best by keywords and by meaning


def test_what_a_found_item_points_to_comes_with_it(con):
    found = ids(con, "law", [1, 0])
    assert "table.dim_person" in found  # glossary.law maps to one of its columns
    assert "rule.recess" in found  # glossary.law's see_also
