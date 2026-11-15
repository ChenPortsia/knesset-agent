"""run_sql must refuse what it shouldn't run, not rely on the agent behaving."""

import duckdb
import pytest

from tools import sql
from tools.db import connect, data_version
from tools.sql import Refused, run_sql, sample_values


@pytest.fixture
def con(tmp_path):
    path = tmp_path / "knesset.duckdb"
    with duckdb.connect(str(path)) as setup:
        setup.execute(
            """
            CREATE SCHEMA gold;
            CREATE TABLE gold.dim_person AS
            SELECT * FROM (VALUES (1, 'בנימין נתניהו'), (2, 'יאיר לפיד'), (3, 'יאיר גולן'))
                AS t (person_id, full_name);
            CREATE TABLE build_info AS SELECT '2026-11-15T00:00:00Z' AS data_version;
            """
        )
    (tmp_path / "secret.csv").write_text("password\nhunter2\n")
    return connect(path)


def test_a_select_runs_on_gold_without_the_schema_name(con):
    result = run_sql(con, "SELECT full_name FROM dim_person WHERE person_id = 2")
    assert result == {"columns": ["full_name"], "rows": [("יאיר לפיד",)], "truncated": False}


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE gold.dim_person",
        "UPDATE gold.dim_person SET full_name = 'x'",
        "CREATE TABLE gold.x AS SELECT 1",
        "ATTACH 'other.duckdb'",
        "SET enable_external_access = true",
        "SELECT 1; SELECT 2",
    ],
)
def test_anything_but_one_select_is_refused(con, statement):
    with pytest.raises(Refused):
        run_sql(con, statement)


def test_sql_cannot_read_files(con, tmp_path):
    with pytest.raises(duckdb.Error):
        run_sql(con, f"SELECT * FROM read_csv('{tmp_path / 'secret.csv'}')")


def test_long_results_are_cut_and_say_so(con, monkeypatch):
    monkeypatch.setattr(sql, "MAX_ROWS", 2)
    result = run_sql(con, "SELECT * FROM dim_person ORDER BY person_id")
    assert len(result["rows"]) == 2 and result["truncated"]


def test_a_slow_query_is_stopped(con, monkeypatch):
    monkeypatch.setattr(sql, "TIMEOUT_SECONDS", 0.2)
    with pytest.raises(Refused, match="stopped"):
        run_sql(con, "SELECT COUNT(*) FROM range(100000000000) AS a, range(1000) AS b")


def test_sample_values_checks_how_a_name_is_spelled(con):
    assert sample_values(con, "dim_person.full_name", like="יאיר") == [
        ("יאיר גולן", 1),
        ("יאיר לפיד", 1),
    ]
    with pytest.raises(Refused):
        sample_values(con, "dim_person.password")
    with pytest.raises(Refused):
        sample_values(con, "dim_person.full_name; DROP TABLE x")


def test_data_version(con):
    assert data_version(con) == "2026-11-15T00:00:00Z"
