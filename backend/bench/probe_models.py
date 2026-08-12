"""Prove a model can call **our** tools, with **our** schemas, over OpenRouter.

    python -m bench.probe_models                       # both configured models
    python -m bench.probe_models --model some/model

Task 0 read `tools` out of the catalogue's `supported_parameters` and stopped
there. That is a vendor's claim about a capability, not evidence that this
particular model will emit a well-formed call against the fourteen schemas in
`app/tools.py` — and the money path depends on it completely: a bill-photo turn
ends in `propose_meal`, so a vision model that "supports tools" but mangles a
nested `items` array breaks the ledger just as thoroughly as one that cannot see.

Three schema shapes are probed, chosen because they are the ones a provider is
most likely to mistranslate. All three come from the **live** `app.tools` module,
not from a copy, so this cannot drift from what ships:

1. `propose_meal` — a nested `items` array of objects, plus the `discount_split`
   **enum**. Design §6 warns that `Type.Union`/`Type.Literal` breaks Google's API
   and that `StringEnum` is required; this checks the JSON-Schema side of that
   claim per model.
2. `update_member` — `{"type": ["string", "integer"]}` on `target`, the one
   **union** in the whole set (`tools.py:193`) and the single case the design says
   a naive converter silently mistranslates.
3. `settle_period` — the 7-value `keyword` enum shared by four tools.

What counts as a pass is deliberately strict: a tool call must come back, name the
right tool, parse as JSON, carry every `required` property, use the right JSON
types, and choose only from `enum` where one is declared. A model that answers in
prose instead of calling the tool **fails** — that is the failure mode that would
put arithmetic back on the model's side of the wire.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from pathlib import Path
from urllib.request import Request, urlopen

from bench.judge import KEY_ENV, OPENROUTER_URL

_HERE = Path(__file__).resolve().parent

#: The TỔNG CỘNG printed on `bench/corpus/bills/bill-itemized.png`.
BILL_TOTAL = 154_000

#: The models the design settles on (plan, Task 0).
DEFAULT_MODELS = ("~deepseek/deepseek-v4-flash-latest", "meta/muse-glimmer-30b")

#: `(tool, message, [required properties to check])` per probe. The messages are
#: Vietnamese because every real turn is.
PROBES = (
    ("propose_meal",
     "Tôi trả 154k, ai ăn nấy trả: An cơm tấm 45k, Bình matcha 55k, Cường hồng trà 39k. "
     "Phần giảm chia đều. An là member 1, Bình 2, Cường 3.",
     ("participants", "total")),
    ("update_member",
     "Đổi tên thành viên có nickname 'binh' thành 'Bình Nguyễn'.",
     ("target",)),
    ("settle_period",
     "Tuần này ai nợ ai bao nhiêu?",
     ()),
)


def _tool_schemas() -> dict[str, dict]:
    """The live schemas, straight out of `app.tools`."""
    from app.db import Database
    from app.tools import ToolContext, build_tools
    ctx = ToolContext(db=Database("sqlite:///:memory:"), room_id=1)
    return {name: tool.input_schema for name, tool in build_tools(ctx).items()}


def _as_openai_tool(name: str, schema: dict, description: str) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": description, "parameters": schema}}


#: Upstream 429s are common on shared-capacity routing and say nothing about
#: whether a model can call tools, so they are retried rather than recorded as a
#: capability failure.
_RETRIES = 4
_BACKOFF = 3.0


def _post(payload: dict, key: str, timeout: float = 90.0, *, sleep=None) -> dict:
    import time
    sleep = sleep or time.sleep
    request_body = json.dumps(payload).encode()
    last: Exception | None = None
    for attempt in range(_RETRIES):
        request = Request(OPENROUTER_URL, data=request_body,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.loads(response.read().decode())
        except HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == _RETRIES - 1:
                raise
            sleep(_BACKOFF * (attempt + 1))
        except (URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == _RETRIES - 1:
                raise
            sleep(_BACKOFF * (attempt + 1))
    raise last  # unreachable, but keeps the contract explicit


_JSON_TYPES = {"string": str, "integer": int, "boolean": bool,
               "array": list, "object": dict, "number": (int, float)}


def _is_type(value, declared: str) -> bool:
    """Does `value` satisfy one JSON-Schema type name?

    `bool` is a subclass of `int` in Python, so a plain `isinstance` check accepts
    `true` for an `integer` field — including inside a `["string","integer"]`
    union, which is where the first version of this let it through.
    """
    expected = _JSON_TYPES.get(declared)
    if expected is None:
        return True
    if declared in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def validate_args(schema: dict, args: dict, required_extra=()) -> list[str]:
    """Check `args` against `schema`. Returns problems; empty means valid.

    Only the six keywords the schemas actually use (design §6) are enforced —
    `type`, `properties`, `required`, `items`, `enum` — because anything else in a
    reply is the model volunteering something the tool will ignore anyway.
    """
    problems: list[str] = []

    def check(node: dict, value, path: str) -> None:
        declared = node.get("type")
        if isinstance(declared, list):
            # The union case: {"type": ["string", "integer"]}
            if not any(_is_type(value, t) for t in declared if t in _JSON_TYPES):
                problems.append(f"{path}: {value!r} matches none of {declared}")
            return
        expected = _JSON_TYPES.get(declared)
        if expected and not _is_type(value, declared):
            problems.append(f"{path}: expected {declared}, got {type(value).__name__} {value!r}")
            return
        if node.get("enum") is not None and value not in node["enum"]:
            problems.append(f"{path}: {value!r} not in enum {node['enum']}")
        if declared == "object":
            for key in node.get("required") or []:
                if key not in (value or {}):
                    problems.append(f"{path}.{key}: required but absent")
            for key, child in (node.get("properties") or {}).items():
                if isinstance(value, dict) and key in value:
                    check(child, value[key], f"{path}.{key}")
        if declared == "array" and node.get("items") and isinstance(value, list):
            for index, entry in enumerate(value):
                check(node["items"], entry, f"{path}[{index}]")

    check(schema, args, "args")
    for key in required_extra:
        if key not in args:
            problems.append(f"args.{key}: expected the model to supply it")
    return problems


def _bill_message() -> list[dict] | None:
    """The synthetic bill, as an OpenAI-style image content block.

    A vision model that reads the photo but cannot then call `propose_meal` breaks
    the money path exactly as thoroughly as one that cannot see, so the two have to
    be probed **together** rather than separately (design §12).
    """
    import base64
    path = _HERE / "corpus" / "bills" / "bill-itemized.png"
    if not path.exists():
        return None
    data = base64.b64encode(path.read_bytes()).decode()
    return [
        {"type": "text",
         "text": "Đây là bill. Ghi bữa này: ai ăn nấy trả, tên ghi trên từng món. "
                 "An là member 1, Bình 2, Cường 3. Tôi (An) trả."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
    ]


def probe_vision(model: str, key: str, schemas: dict[str, dict]) -> dict:
    """Read a real bill image and end in a well-formed `propose_meal` call.

    `bill-itemized.png` prints TỔNG CỘNG 154,000đ over three named items, so the
    total is checkable rather than a judgement call.
    """
    from app.db import Database
    from app.tools import ToolContext, build_tools
    tools = build_tools(ToolContext(db=Database("sqlite:///:memory:"), room_id=1))

    content = _bill_message()
    row = {"model": model, "tool": "propose_meal+vision", "ok": False, "detail": ""}
    if content is None:
        row["detail"] = "bench/corpus/bills/bill-itemized.png is missing"
        return row

    payload = {
        "model": model, "temperature": 0, "max_tokens": 900,
        "messages": [
            {"role": "system", "content": "Bạn là bot chia tiền ăn trưa. Luôn dùng tool để ghi số."},
            {"role": "user", "content": content},
        ],
        "tools": [_as_openai_tool("propose_meal", schemas["propose_meal"],
                                  tools["propose_meal"].description)],
        "tool_choice": "auto",
    }
    try:
        body = _post(payload, key)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        detail = getattr(exc, "read", lambda: b"")()[:200].decode("utf-8", "replace")
        row["detail"] = f"transport: {exc} {detail}".strip()
        return row

    message = ((body.get("choices") or [{}])[0].get("message") or {})
    calls = message.get("tool_calls") or []
    if not calls:
        row["detail"] = f"no tool call; replied: {(message.get('content') or '')[:140]!r}"
        return row
    try:
        args = json.loads((calls[0].get("function") or {}).get("arguments") or "{}")
    except json.JSONDecodeError as exc:
        row["detail"] = f"arguments are not JSON: {exc}"
        return row

    problems = validate_args(schemas["propose_meal"], args, ("participants", "total"))
    if args.get("total") != BILL_TOTAL:
        problems.append(f"total: bill prints {BILL_TOTAL}, model read {args.get('total')!r}")
    row["ok"] = not problems
    row["detail"] = "; ".join(problems) if problems else json.dumps(args, ensure_ascii=False)[:150]
    return row


def probe(model: str, key: str, schemas: dict[str, dict]) -> list[dict]:
    """Run every probe against one model. Returns a row per probe."""
    from app.tools import ToolContext, build_tools
    from app.db import Database
    tools = build_tools(ToolContext(db=Database("sqlite:///:memory:"), room_id=1))

    rows = []
    for name, message, required_extra in PROBES:
        schema = schemas[name]
        payload = {
            "model": model, "temperature": 0, "max_tokens": 800,
            "messages": [
                {"role": "system",
                 "content": "Bạn là bot chia tiền ăn trưa. Luôn dùng tool để ghi số. "
                            "Thành viên: An id 1, Bình id 2, Cường id 3."},
                {"role": "user", "content": message},
            ],
            "tools": [_as_openai_tool(name, schema, tools[name].description)],
            "tool_choice": "auto",
        }
        row = {"model": model, "tool": name, "ok": False, "detail": ""}
        try:
            body = _post(payload, key)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            detail = getattr(exc, "read", lambda: b"")()[:200].decode("utf-8", "replace")
            row["detail"] = f"transport: {exc} {detail}".strip()
            rows.append(row)
            continue

        choice = ((body.get("choices") or [{}])[0].get("message") or {})
        calls = choice.get("tool_calls") or []
        if not calls:
            # Answering in prose is the failure that puts arithmetic back on the
            # model's side of the wire.
            row["detail"] = f"no tool call; replied: {(choice.get('content') or '')[:120]!r}"
            rows.append(row)
            continue

        call = calls[0].get("function") or {}
        if call.get("name") != name:
            row["detail"] = f"called {call.get('name')!r}, expected {name!r}"
            rows.append(row)
            continue
        try:
            args = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            row["detail"] = f"arguments are not JSON: {exc}"
            rows.append(row)
            continue

        problems = validate_args(schema, args, required_extra)
        row["ok"] = not problems
        row["detail"] = "; ".join(problems) if problems else json.dumps(
            args, ensure_ascii=False)[:150]
        rows.append(row)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench.probe_models", description=__doc__)
    parser.add_argument("--model", action="append", dest="models",
                        help="repeatable; defaults to the two configured models")
    parser.add_argument("--vision", action="append", dest="vision_models",
                        help="also read a real bill image and end in propose_meal")
    args = parser.parse_args(argv)

    key = os.environ.get(KEY_ENV)
    if not key:
        print(f"{KEY_ENV} is not set", file=sys.stderr)
        return 2

    schemas = _tool_schemas()
    failures = 0
    for model in args.models or DEFAULT_MODELS:
        print(f"\n=== {model}")
        for row in probe(model, key, schemas):
            mark = "PASS" if row["ok"] else "FAIL"
            failures += 0 if row["ok"] else 1
            print(f'  {mark}  {row["tool"]:19s} {row["detail"]}')
    for model in args.vision_models or ():
        print(f"\n=== {model} (vision)")
        row = probe_vision(model, key, schemas)
        failures += 0 if row["ok"] else 1
        print(f'  {"PASS" if row["ok"] else "FAIL"}  {row["tool"]:19s} {row["detail"]}')
    print(f'\n{failures} failure(s)')
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
