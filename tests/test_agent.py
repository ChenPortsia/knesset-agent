"""The parts of the agent that need no model: what it is shown, and what is logged."""

import duckdb

from ops import runlog
from tools.context import render


def test_render_keeps_ids_and_what_the_model_needs():
    text = render(
        [
            {"id": "alias.party.likud", "kind": "alias", "title": "", "text": "", "text_he": "",
             "payload": {"names": ["ליכוד"], "note": None,
                         "resolves_to": {"column": "dim_faction.faction_id", "values": [1096]}}},
            {"id": "table.dim_bill", "kind": "table", "title": "dim_bill", "text": "Bills",
             "text_he": "", "payload": {
                 "columns": [{"name": "is_passed", "type": "boolean", "text": "Became law"}],
                 "joins": ["dim_bill.committee_id -> dim_committee.committee_id"]}},
            {"id": "vq.x", "kind": "verified_query", "title": "", "text": "How many?",
             "text_he": "", "payload": {"sql": 'SELECT 1 FROM "warehouse"."gold"."dim_bill"'}},
        ]
    )  # fmt: skip
    assert "[alias.party.likud] ליכוד = dim_faction.faction_id IN [1096]" in text
    assert "is_passed boolean: Became law" in text
    assert "join: dim_bill.committee_id -> dim_committee.committee_id" in text
    assert 'SELECT 1 FROM "dim_bill"' in text  # no warehouse prefix: the agent's file has none


def test_a_run_and_its_steps_are_saved_with_shared_ids(tmp_path):
    run = {
        "run_id": "r1", "trace_id": "t1", "session_id": "s1", "user_id": "u1",
        "data_version": "2026-11-15T00:00:00Z", "model": "fake:model", "question": "?",
        "kind": "answer", "text": "42", "sql": "SELECT 42", "context_ids": ["table.dim_bill"],
        "input_tokens": 10, "output_tokens": 2, "seconds": 1.5,
    }  # fmt: skip
    steps = [{"tool": "run_sql", "input": {"query": "SELECT 42"}, "output": "[[42]]"}]
    runlog.save(run, steps, tmp_path / "agent_ops.duckdb")

    with duckdb.connect(str(tmp_path / "agent_ops.duckdb")) as con:
        assert con.execute(
            "SELECT trace_id, data_version, kind, context_ids FROM ops.agent_runs"
        ).fetchall() == [("t1", "2026-11-15T00:00:00Z", "answer", ["table.dim_bill"])]
        assert con.execute("SELECT run_id, step, tool FROM ops.agent_steps").fetchall() == [
            ("r1", 1, "run_sql")
        ]
