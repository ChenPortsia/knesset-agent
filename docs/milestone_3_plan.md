# Milestone 3 plan: a single SQL agent, and its test

**Goal.** One agent that answers structured questions about the Knesset: it reads the context,
writes SQL over the gold tables, runs it, and answers in the user's language with the numbers
it found.

**Done when** (from `ARCHITECTURE.md`): at least 80% of the structured questions in the
question bank are answered correctly, with a trace of every run in Langfuse.

**Not the goal.** Several agents, a planner, a critic, text search, a chat UI, learning. Those
are milestones 4 to 7. One agent first: it sets the baseline the team has to beat.

## What we already have

| Piece | Where | Used for |
| --- | --- | --- |
| Gold star schema (20 tables, fixed contracts) | knesset_data | the only data the agent may query |
| `get_context(question)` | `tools/context.py` | the tables, terms, rules, metrics and example queries for a question |
| 21 verified queries with expected results | knesset_data `context/` | examples for the agent, and ground truth for the test |
| 43 questions labelled with the context they need | `evals/question_bank.yaml` | the start of the answer test |

## Decisions

1. **One ReAct agent in LangGraph.** A loop: the model thinks, calls a tool, reads the result,
   and repeats until it can answer. LangGraph's ready-made agent does this; we write the
   tools and the prompt. No custom graph yet (that is milestone 5).
2. **Four narrow tools**, each a small typed Python function:

   | Tool | Does | Limits |
   | --- | --- | --- |
   | `get_context(question)` | returns the context items for the question | exists |
   | `run_sql(sql)` | runs one query on gold | one `SELECT` only, gold tables only, 200 rows, 30 seconds, read-only file |
   | `sample_values(column, like)` | distinct values of a gold column | to check a name or a code before filtering on it |
   | `odata_query(table, filter)` | reads the live Knesset API | a fixed list of tables, 50 rows, for facts newer than the last load |

3. **The context is given, not optional.** The harness calls `get_context` before the first
   model turn and puts the result in the prompt, so the agent can't skip it. The tool stays
   available for follow-up searches.
4. **SQL safety is enforced in code, not asked for in the prompt.** `run_sql` parses the query
   with DuckDB and refuses anything that isn't a single `SELECT` on gold tables. The warehouse
   is opened read-only, with file and network access from SQL turned off.
5. **Answers come with their evidence.** The final answer is a small structure: the answer
   text, the SQL that produced it, the result rows, and the context item ids used. The test
   grades it; later the Critic and the UI use it.
6. **Metrics: SQL first.** MetricFlow needs the dbt project next to the agent, which is a
   heavy dependency for the agent's container. In this milestone the agent answers metric
   questions with SQL, guided by the metric's definition and its verified query. A
   `query_metric` tool is added only if the test shows metric questions failing.
7. **The test compares with ground truth.** Each test question has a ground-truth SQL query
   (written and checked by hand, many reused from the verified queries). The agent's answer
   is graded against the query's result by a small judge model that sees the question, the
   true result and the answer, and says pass or fail with a reason. We check the judge
   itself on 20 hand-graded answers before trusting it.
8. **Ambiguous questions have a right answer too:** a clarifying question. A few are in the
   test from the start.
9. **Langfuse cloud (free tier) for traces**, through its LangChain callback: every model
   turn and tool call, with tokens, cost and latency. Self-hosting can come with the pilot.
10. **Runs are also logged to our own tables**, as `CLAUDE.md` asks: `ops.agent_runs` and
    `ops.agent_steps` in a small local DuckDB file, with `run_id`, `trace_id`, `session_id`,
    `user_id` and `data_version`. Postgres replaces the file at the pilot.
11. **Any model, set in one value.** The model is named in `.env` as `provider:model`
    (`KNESSET_MODEL`), and LangChain loads the matching client, so moving between Gemini and
    Claude is a one-line change with no code change. We start on Gemini (your credits), and
    step 10 compares models on the same test.
12. **The agent has its own database.** knesset_data's `make publish` copies gold and context
    into `data/published/knesset.duckdb` and stamps it with a data version. The agent opens
    only that file, read-only, so silver and bronze are out of reach by construction.

## Repo layout after this milestone

```
knesset-agent/
  agents/sql_analyst.py     # the agent: prompt, tools, answer structure
  tools/context.py          # get_context (done)
  tools/sql.py              # run_sql, sample_values
  tools/odata.py            # odata_query
  ops/runlog.py             # agent_runs, agent_steps
  evals/question_bank.yaml  # questions: context needs (done) + ground truth
  evals/eval_context.py     # the context test (done)
  evals/eval_answers.py     # the answer test: run, grade, report
  evals/judge.py            # the grader
  config.toml               # model, limits, paths
  __main__.py               # python -m knesset_agent "question"  (make ask Q=...)
```

## Steps

| # | Step | Done when |
| --- | --- | --- |
| 1 | **Published database with a data version** (in knesset_data): `make publish` | done |
| 2 | **`run_sql` and `sample_values`** with their limits | done: tests show a write, two statements, a file read and a slow query are all refused |
| 3 | **Test questions with ground truth**: about 40 structured questions across lookups, aggregates, votes, oversight, trends and ambiguous, each with checked SQL | done: 38 in `evals/answer_bank.yaml`, 7 held out; `make eval-truth` runs them all |
| 4 | **The agent**: prompt, tools, answer structure, `make ask Q="..."` | built; answered 5 of 5 trial questions correctly. A full hand-check waits for a key with quota |
| 5 | **Langfuse traces and `ops` logging** | done: a question is one trace and one `agent_runs` row with the same `trace_id` |
| 6 | **The judge**, checked on 20 hand-graded answers | it agrees with the hand grades on at least 18 |
| 7 | **`make eval-answers`**: run the bank, grade, report by category, with cost and time | built; the baseline waits for a key with quota (the free tier allows 20 requests a day) |
| 8 | **Improve to 80%**: read the failures; fix context first (a rule, a term, a verified query), the prompt second, tools last | at least 80%, and each fix noted |
| 9 | **`odata_query`** and 3 to 5 freshness questions | the agent uses it only when the warehouse is too old to answer |
| 10 | **Cheaper models** on the same test | a table of score, cost and time per model; a default chosen |
| 11 | **Docs**: README, `ARCHITECTURE.md` | someone new can ask a question and run the test |

Steps 2 to 4 are the core. Step 8 is where most of the learning is: every failure is a
missing piece of context, an unclear tool, or a real limit of the data.

## Risks and how we handle them

| Risk | Handling |
| --- | --- |
| The agent writes plausible but wrong SQL (wrong join, "ever" vs "at the time") | rules and verified queries in the context; the test catches it; failures become new rules |
| The judge is wrong | checked against hand grades first; borderline cases are listed for review, not hidden |
| Tuning to the test | hold back a quarter of the questions: never read their failures, only their score |
| Cost of running the test | measure after the first full run; cache the context; use a cheaper model for the judge |
| The agent queries outside gold or changes data | impossible by construction: read-only file, parser check, one statement |
| Hebrew names don't match (spelling, prefixes) | aliases first, then `sample_values` to confirm before filtering |
| Answers go stale between loads | every answer carries the data version and the newest date it saw |

## Not in milestone 3

- Several agents, a planner, a critic (milestone 5); protocol text (4); learning (7).
- A chat UI and conversation memory. The agent answers one question at a time.
- MetricFlow as a tool, unless step 8 shows it is needed.
- Postgres and deployment (the pilot).

## Keys

All keys live in `.env` on your machine (see `.env.example`); the file is never committed.

| Variable | For |
| --- | --- |
| `KNESSET_MODEL` | which model answers, as `provider:model` |
| `GOOGLE_API_KEY` | Gemini |
| `ANTHROPIC_API_KEY` | Claude (only when `KNESSET_MODEL` names a Claude model) |
| `KNESSET_JUDGE_MODEL` | which model grades answers in the test (default: the same) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | traces |
