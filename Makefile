# Reads the database published by knesset_data (../knesset_data/data/published/knesset.duckdb,
# or set KNESSET_DATABASE). Run `make context publish` there first.

.PHONY: ask queries ui context eval-context eval-truth eval-answers test lint

ask:  ## Ask the agent a question, e.g. make ask Q="כמה חוקים עברו בכנסת ה-25?"
	uv run python -W ignore -m agents.sql_analyst "$(Q)"

queries:  ## The last 30 queries the agent sent: when, which model, whether it worked
	uv run python -m ops.runlog

ui:  ## Browse the agent's runs, queries and test results, and the data, in the browser (read-only)
	uv run python -m scripts.ui

context:  ## Show the context a question gets, e.g. make context Q="מי המורדים בליכוד?"
	uv run python -m tools.context "$(Q)"

eval-context:  ## Does get_context find what each question in the bank needs? (V=1 for details)
	uv run python -m evals.eval_context $(if $(V),-v)

eval-truth:  ## Does every ground-truth query in the answer bank run? (V=1 to see the results)
	uv run python -m evals.check_truth $(if $(V),-v)

eval-answers:  ## The answer test: ask every question in the answer bank and grade it (ARGS="--limit 5")
	uv run python -W ignore -m evals.eval_answers $(ARGS)

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .
