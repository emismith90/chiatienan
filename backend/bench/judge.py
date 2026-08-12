"""The prose judge: one pinned model, called over OpenRouter, engine-independent.

`grade_prose` takes its judge as an argument and never builds one, so the graders
stay offline and testable. This is where the real one is built.

**Deliberately not the engine under test.** The judge has to be pinned across the
Cursor baseline *and* the Pi run — a baseline graded with no judge, or a different
one, is not a comparison (design §11.5). Routing it through OpenRouter rather than
through whichever engine is in the tree means the same `BENCH_JUDGE_MODEL` answers
both runs, before and after the cutover.

`urllib` rather than a client library: this is one POST, and `bench/` must not add
a production dependency for a development tool.

The key lives in **`OPEN_ROUTER_KEY`**. That is the name the environment actually
uses; design §10's `OPENROUTER_API_KEY` was an assumption and is wrong. One
canonical name, no aliases — the sidecar (Task 13) needs the same variable, and a
second accepted spelling is how half a deployment ends up unauthenticated.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: The environment's actual name for the OpenRouter credential.
KEY_ENV = "OPEN_ROUTER_KEY"

#: Roughly two sentences of reply. The rubric asks for JSON, not an essay.
MAX_TOKENS = 200


def _post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed host
        return json.loads(response.read().decode())


def _extract_json(text: str) -> dict | None:
    """Pull the verdict object out of a reply that may be fenced or chatty."""
    for candidate in (text, *re.findall(r"\{.*?\}", text or "", re.DOTALL)):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and "ok" in parsed:
            return parsed
    return None


def build_prompt(case, record: dict, rubric: str) -> str:
    """The judge sees the user's message and the reply — nothing else.

    Not the expected tools, not the golden numbers: the question is whether this
    reply is a good reply, and showing the answer key invites the judge to grade
    correctness it is not being asked about.
    """
    return (f"{rubric}\n"
            f"--- Người dùng ---\n{case.message}\n"
            f"--- Bot ---\n{record.get('final_text') or ''}\n")


def openrouter_judge(model: str, *, api_key: str | None = None, post=_post,
                     timeout: float = 60.0):
    """Return a `judge(case, record, rubric)` callable for `grade_prose`.

    Any failure — no key, a transport error, an unparseable reply — returns a
    dict **without** `ok`, which `grade_prose` records as *not graded*. That is
    deliberate: a judge that silently passed on error would turn an outage into a
    clean bill of health, and a judge that failed on error would blame the engine
    for the harness's own problem.
    """
    key = api_key if api_key is not None else os.environ.get(KEY_ENV, "")

    def judge(case, record, rubric):
        if not key:
            return {"error": f"{KEY_ENV} is not set"}
        payload = {"model": model, "max_tokens": MAX_TOKENS, "temperature": 0,
                   "messages": [{"role": "user",
                                 "content": build_prompt(case, record, rubric)}]}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            body = post(OPENROUTER_URL, payload, headers, timeout)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return {"error": f"judge transport failed: {exc}"}
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return {"error": f"judge returned an unexpected body: {body!r}"}
        verdict = _extract_json(text)
        return verdict if verdict is not None else {"error": f"judge said: {text!r}"}

    judge.model = model
    return judge
