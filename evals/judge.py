"""The grader: does an answer agree with the true result?

A model reads the question, the ground-truth rows and the agent's answer, and says pass or
fail with a reason. Set KNESSET_JUDGE_MODEL to grade with another model than the one tested.
"""

import json
import os
from typing import Any

from pydantic import BaseModel, Field

from tools.model import load_model

PROMPT = """\
You grade an analyst's answer about the Israeli Knesset against the true result of a database
query. The true result is correct by definition.

Pass the answer only if what it states agrees with the true result:
- Every number the question asks for matches the true result. Rounding is fine (69.7% vs 70%).
- For "which / who ... the most" questions, the items the answer names as top must be the top
  rows of the true result, in the same order. The answer may list fewer rows than the result.
- Names may be written in Hebrew or English, in full or short form.
- Extra correct detail, caveats and wording do not matter. Do not use outside knowledge.
Fail it if a requested number or name is missing, different, or the answer says it cannot answer.

Question:
{question}

True result (columns, then up to {shown} rows):
{truth}

Answer to grade:
{answer}
"""
ROWS_SHOWN = 30


class Verdict(BaseModel):
    reason: str = Field(description="One sentence: what matched or what differed")
    passed: bool


def judge(question: str, truth: dict[str, Any], answer: str, model: str | None = None) -> Verdict:
    model = model or os.environ.get("KNESSET_JUDGE_MODEL") or None
    shown = json.dumps(
        {"columns": truth["columns"], "rows": truth["rows"][:ROWS_SHOWN]},
        ensure_ascii=False, default=str,
    )  # fmt: skip
    prompt = PROMPT.format(question=question, shown=ROWS_SHOWN, truth=shown, answer=answer)
    return load_model(model).with_structured_output(Verdict).invoke(prompt)
