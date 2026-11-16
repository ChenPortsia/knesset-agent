# knesset-agent

A LangGraph multi-agent system that answers questions about the Knesset, reading the warehouse and context layer built by [knesset_data](https://github.com/ChenPortsia/knesset_data). See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and milestones.

**So far:** `get_context` (milestone 2, done), and the first agent of milestone 3: a SQL analyst that answers structured questions from the gold tables, with a trace of every run. Plan and status: [docs/milestone_3_plan.md](docs/milestone_3_plan.md).

## Layout

```
agents/sql_analyst.py      # the agent: prompt, tools, answer structure; ask(question)
ops/runlog.py              # agent_runs and agent_steps (data/agent_ops.duckdb), Langfuse trace ids
tools/db.py                # the published database, read-only, and its data version
tools/context.py           # get_context(question) -> the context items for a question
tools/odata.py             # odata_query, odata_fields: the live Knesset API, 11 tables, 50 rows
tools/sql.py               # run_sql (one SELECT, 200 rows, 30 seconds) and sample_values
evals/question_bank.yaml   # questions, each with the items it needs
evals/eval_context.py      # does get_context find them?
evals/answer_bank.yaml     # questions with ground-truth SQL, for the answer test
evals/check_truth.py       # does every ground-truth query run?
evals/eval_answers.py      # the answer test: ask, grade, report
evals/judge.py             # the grader
tests/                     # unit tests on a tiny made-up context (no model, no warehouse)
```

## Quick start

Needs the database knesset_data publishes: run `make context publish` there. It holds only the gold tables and the context, so the agent can't reach anything else. By default it is read from `../knesset_data/data/published/knesset.duckdb`; set `KNESSET_DATABASE` to use another path.

Keys go in a `.env` file (never committed): copy `.env.example` and fill it in.

```bash
uv sync
make context Q="מי המורדים הגדולים בליכוד?"   # the context one question gets
make eval-context                             # the test on the whole question bank (V=1 for details)
make eval-truth                               # every ground-truth query in the answer bank runs
make ask Q="כמה חוקים עברו בכנסת ה-25?"      # ask the agent (needs keys in .env)
make eval-answers                             # the answer test (about 200 model calls)
make test lint
```

The first run downloads the embedding model (2.2 GB, once).

## How get_context works

A question gets, in this order:

| Step | What | Why |
| --- | --- | --- |
| 1 | Every **alias** whose name appears in the question | Exact matching: "ליכוד" must resolve to Likud's faction ids, never to a guess |
| 2 | The **rules marked `always`** | A few rules apply to every question (gold only, use metrics, scope from 2015) |
| 3 | The best matches of each kind: 4 tables, 4 glossary terms, 3 rules, 3 metrics, 3 verified queries | Search by keywords (BM25) and by meaning (embeddings); the two rankings are merged |
| 4 | What those matches **point to**: a glossary term's or a metric's tables, `see_also` items | The context already says which tables a term lives in; no need to search for them |

A column that matches counts for its table, and a table comes with all its columns.

**Merging two rankings.** Keyword search finds exact words (names, terms); embeddings find meaning ("answer queries" → reply rate). Each item scores `1 / (60 + position)` in each ranking and the two are added (reciprocal rank fusion), so an item high in either list does well and an item high in both does best.

## The test

`evals/question_bank.yaml` has 43 questions (half Hebrew, half English), each with the items an analyst would need. The labels were written before the search was run. `make eval-context` fails if any needed item is missing.

Result: **43 of 43 questions get everything they need** (128 of 128 items), with about 25 items returned per question.

How the search was chosen, on the first 34 questions (needed items found, aliases and standing rules aside):

| Set-up | Found |
| --- | --- |
| Keywords only | 68% |
| Small model (MiniLM, 0.2 GB) only | 78% |
| Small model + keywords | 86% |
| Large model (multilingual-e5-large, 2.2 GB) + keywords | 91% |
| Large model + keywords + following links | 100% |

Stripping Hebrew prefixes and plural endings for keyword search was tried and didn't help, so it isn't used.

The set-up was picked using this bank, so 100% is the best case. New questions are the real test: when 9 were added, 4 of them missed an item at first. Each was fixed in the context, not in the search: a synonym on a glossary term, or another phrasing (`also_asked`) on a verified query. Do the same as people ask new questions: add the question to the bank, and fix a miss by improving the context.

## The agent

`make ask` runs one question through the SQL analyst (`agents/sql_analyst.py`):

1. Code fetches the context for the question and puts it in the first message.
2. The model works in a loop with its tools: `run_sql`, `sample_values`, `get_context` (for a second, more specific search), and `odata_query` with `odata_fields` (the live Knesset API, only for facts newer than the last load).
3. It ends with a structured answer: its kind (`answer`, `clarify` or `out_of_scope`), the text in the question's language, the SQL behind it and the context items it used.

Every run is logged to `ops.agent_runs` and `ops.agent_steps` in `data/agent_ops.duckdb`, and traced in Langfuse under the same `trace_id`. The model is the `KNESSET_MODEL` line in `.env`, written `provider:model`; changing provider is changing that line and its key.

`make eval-answers` asks every question in `evals/answer_bank.yaml` and grades the answers against ground truth. One question takes about 5 model calls, so the whole test needs a key with real quota: Gemini's free tier allows 20 requests a day per model.
