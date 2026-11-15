# Reads the warehouse built by knesset_data (../knesset_data/data/warehouse/warehouse.duckdb,
# or set KNESSET_WAREHOUSE). Run `make context` there first.

.PHONY: context eval-context test lint

context:  ## Show the context a question gets, e.g. make context Q="מי המורדים בליכוד?"
	uv run python -m tools.context "$(Q)"

eval-context:  ## Does get_context find what each question in the bank needs? (V=1 for details)
	uv run python -m evals.eval_context $(if $(V),-v)

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .
