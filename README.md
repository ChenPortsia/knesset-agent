# knesset-agent

A LangGraph multi-agent system that answers questions about the Knesset, reading the warehouse and context layer built by [knesset_data](https://github.com/ChenPortsia/knesset_data). See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and milestones.

**So far:** `get_context` (milestone 2, done), and the first agent of milestone 3: a SQL analyst that answers structured questions from the gold tables, with a trace of every run. Plan and status: [docs/milestone_3_plan.md](docs/milestone_3_plan.md).

## Layout

```
agents/sql_analyst.py      # the agent: prompt, tools, answer structure; ask(question)
ops/runlog.py              # agent_runs and agent_steps (data/agent_ops.duckdb), Langfuse trace ids
tools/db.py                # the published database, read-only, and its data version
tools/context.py           # get_context(question) -> the context items for a question
tools/search.py            # search_protocols: what was said in committee, by keywords and meaning
tools/odata.py             # odata_query, odata_fields: the live Knesset API, 11 tables, 50 rows
tools/sql.py               # run_sql (one SELECT, 50 rows, 30 seconds) and sample_values
evals/question_bank.yaml   # questions, each with the items it needs
evals/eval_context.py      # does get_context find them?
evals/answer_bank.yaml     # questions with ground-truth SQL, for the answer test
evals/check_truth.py       # does every ground-truth query run?
evals/eval_answers.py      # the answer test: ask, grade, report
evals/judge.py             # the grader
evals/text_bank.yaml       # questions about what was said in committee, with the passage that answers each
evals/eval_text.py         # the text test: is the passage found, is the answer correct and faithful
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
make eval-answers                             # the answer test (about 160 model calls)
make eval-text ARGS="--retrieval-only"         # the text test, search part only: free
make eval-text                                # the text test (about 70 short model calls)
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
2. The model works in a loop with its tools: `run_sql`, `sample_values`, `get_context` (for a second, more specific search), `search_protocols` (passages of committee protocols, with filters for committee, speaker and dates), and `odata_query` with `odata_fields` (the live Knesset API, only for facts newer than the last load).
3. Its last message is the answer. The first line says its kind (`answer`, `clarify` or `out_of_scope`); the text follows in the question's language. The run record adds the last query that worked.

Every run is logged to `ops.agent_runs` and `ops.agent_steps` in `data/agent_ops.duckdb`, and traced in Langfuse under the same `trace_id`. The view `ops.agent_queries` lists every SQL and live-API query the agent sent, with the model that sent it, the question and whether it worked: `make queries` shows the latest, and `make ui` opens all of it in the browser, next to the data and the saved test results (`eval_results`). The model is the `KNESSET_MODEL` line in `.env`, written `provider:model`; changing provider is changing that line and its key.

`make eval-answers` asks every question in `evals/answer_bank.yaml` and grades the answers against ground truth.

**What a run costs.** Each question is one conversation of about 3 to 4 model calls (one per query, plus the answer), and each call re-reads the whole conversation so far: about 5,000 tokens of prompt and context, plus every query result. Grading adds one short call for each question that has a numeric truth. A full run is roughly 130 answer calls and 30 grading calls. Keep it cheap:

- grade with a cheap model: `KNESSET_JUDGE_MODEL=anthropic:claude-haiku-5-5`
- after a change, rerun only what failed: `make eval-answers ARGS="--failed"`, or one category: `ARGS="--category votes"`
- try a change on a few questions first: `ARGS="--limit 5"`
- with Claude, the prompt is cached between the calls of a question, and query results are capped at 50 rows

### Result

First full run, on `anthropic:claude-opus-5-5`: **35 of 38 correct (92%)**. The questions used for tuning scored 29 of 31, the held-out ones 6 of 7. About 9 seconds and 17,600 tokens per question.

| Category | Correct |
| --- | --- |
| lookup | 5/5 |
| aggregate | 8/10 |
| votes | 7/8 |
| oversight | 5/5 |
| trend | 5/5 |
| ambiguous | 2/2 |
| out of scope | 3/3 |

After fixing the ground truth and making runs cheaper (prompt caching, results capped at 50 rows, a plain final message), the same test on `anthropic:claude-sonnet-5-5`, graded by `claude-haiku-5-5`:

| Model | All | Tuned on | Held out | Model calls per question | Cost of the run |
| --- | --- | --- | --- | --- | --- |
| `claude-opus-5-5` (first run, before the fixes) | 35/38 (92%) | 29/31 | 6/7 | about 3.4 | about $4 |
| `claude-sonnet-5-5` | 35/38 (92%) | 31/31 | 4/7 | 2.0 | $0.86 |

Sonnet's three misses are all held-out questions, whose failures are not read. Seven questions are too few to tell a real weakness from chance.

In the first run, both failures that were shown turned out to be mistakes in the ground truth, not in the agent:

- **Busiest committee in 2024.** The agent left out cancelled sessions, as the glossary says. The verified query counted them. The query, the monthly one and the `committee_sessions` metric were fixed.
- **MKs least loyal to their faction.** Places 14 and 15 were a tie at one decimal, and the verified query broke the tie arbitrarily. It now orders by the exact share, and the judge accepts tied rows in any order.

Reading them also showed that only about a quarter of committee sessions have an end time, so total committee hours are a large undercount. A rule now tells the agent.

## The text test

`make eval-text` measures search and answer over the committee protocols, on the 27 questions of `evals/text_bank.yaml`. Each question was written from one passage and records where it is. It asks the three things RAGAS asks, measured simply:

| Measure | How | Model calls |
| --- | --- | --- |
| Was the right passage found? | is the labelled passage in the top 1, 3 and 6 results | none |
| Is the answer correct? | a grader compares it with the reference answer | one |
| Is the answer faithful? | the grader checks every claim is in the passages given | (the same call) |

Answering is one model call over the top 6 passages. A question that isn't in Hebrew is first turned into a Hebrew search query by the model, as the agent does itself, because keyword search only matches Hebrew words. It tests search and answer, not the agent's loop.

### Baseline (pilot: 254 sessions, 12,725 chunks, the fast `potion` embedding model)

Search only, questions as asked (13 Hebrew, 14 English), right passage in the top 6:

| Ranking | All | Hebrew | English |
| --- | --- | --- | --- |
| Keywords and meaning merged equally (first version) | 10/27 | 9/13 | 1/14 |
| Keywords only | 10/27 | 9/13 | 1/14 |
| Meaning only (`potion`) | 6/27 | 3/13 | 3/14 |

Keyword search finds Hebrew questions well and English ones not at all; `potion` is weak at meaning in both. Merged equally, the weak ranking pulled good keyword hits down, so with this model keywords count four times as much. The weight is stored with the vectors (`rag.build.keyword_weight`) and is 1 for the large model.

With the English questions rewritten as Hebrew queries by hand, keywords alone found 22 of 27. Those rewrites were written by someone who had seen the passages, so the fair number is the one `make eval-text` gives, where the model writes the query. A trial on 4 questions found all 4 passages and gave 4 correct, faithful answers.

First full run, on `claude-sonnet-5-5`, with the model writing the Hebrew queries. It graded 26 of the 27 questions before the grader crashed on the last one (fixed since):

| Measure | Result |
| --- | --- |
| Right passage in the top 6 | 17/26 (65%) |
| Answers correct | 19/26 (73%) |
| Answers faithful | 23/26 (88%) |

Two answers were correct without the labelled passage: another passage of the same debate held the answer.

### What a strong embedding model would add

The same questions on a reduced set of 1,312 chunks (the labelled passages plus a random tenth of the rest, so every number is easier than on the full pilot), questions as asked:

| Ranking | All | Hebrew | English |
| --- | --- | --- | --- |
| Keywords | 13/27 | 12/13 | 1/14 |
| `potion` | 14/27 | 9/13 | 5/14 |
| `potion` + keywords | 19/27 | 13/13 | 6/14 |
| `e5-large` | 25/27 | 11/13 | 14/14 |
| `e5-large` + keywords | 27/27 | 13/13 | 14/14 |

The large model finds English questions in Hebrew text and rephrased questions that share no words with the passage. It is also about 5,000 times slower to build (0.8 chunks a second on the Mac). How to get that quality at a workable cost is the decision of step 10 in `knesset_data/docs/milestone_4_plan.md`.
