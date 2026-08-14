"""Dump the 17 live tool schemas into the sidecar's test fixture.

    python -m bench.dump_schemas

Two runtimes have to agree about these schemas: Python builds them
(`app/tools.py`) and the Node sidecar converts them to TypeBox
(`agent_sidecar/schema.js`). A hand-copied fixture would drift, and the drift
would show up as the model sending arguments the tool then rejects — a
clarifying question where a logged meal should be.

So the fixture is generated from `build_tools`, committed, and guarded by
`tests/test_tool_schemas_fixture.py`, which fails the moment the live schemas
change without it being regenerated.

Note this must import the real module rather than read the literals: several
schemas share subscripted references — `_PERIOD_SCHEMA["properties"]["keyword"]`
is reused by four tools (`tools.py:841`, `:847`) — and only the imported objects
have those resolved.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "agent_sidecar" / "test" / "fixtures" / "tool-schemas.json"


def live_schemas() -> dict[str, dict]:
    from app.db import Database
    from app.tools import ToolContext, build_tools
    ctx = ToolContext(db=Database("sqlite:///:memory:"), room_id=1)
    return {name: tool.input_schema for name, tool in build_tools(ctx).items()}


def main() -> int:
    schemas = live_schemas()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(schemas, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    enums = sum(1 for s in schemas.values()
                for p in (s.get("properties") or {}).values() if p.get("enum"))
    print(f"wrote {FIXTURE} — {len(schemas)} schemas, {enums} enum properties")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
