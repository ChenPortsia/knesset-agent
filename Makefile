# Reads the database published by knesset_data (../knesset_data/data/published/knesset.duckdb,
# or set KNESSET_DATABASE). Run `make context publish` there first.

.PHONY: context eval-context eval-truth test lint

context:  ## Show the context a question gets, e.g. make context Q="מי המורדים בליכוד?"
	uv run python -m tools.context "$(Q)"

eval-context:  ## Does get_context find what each question in the bank needs? (V=1 for details)
	uv run python -m evals.eval_context $(if $(V),-v)

eval-truth:  ## Does every ground-truth query in the answer bank run? (V=1 to see the results)
	uv run python -m evals.check_truth $(if $(V),-v)

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .
