"""Demo custom tool for the sample Cursor agent + an MCP-server stub.

The tool is trivial on purpose — its value is making the AG-UI TOOL_CALL_*
events fire so the frontend tool-call timeline is exercised. The commented
HttpMcpServerConfig block shows how to attach a real MCP server.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cursor_sdk import CustomTool

_TIME_SCHEMA = {
    "type": "object",
    "properties": {
        "timezone": {"type": "string", "description": "IANA timezone name, e.g. 'Asia/Tokyo'. Defaults to UTC."}
    },
}


def build_demo_tools() -> dict[str, CustomTool]:
    def get_current_time(args: Mapping[str, Any], ctx: Any) -> dict:
        name = str((args or {}).get("timezone") or "UTC").strip() or "UTC"
        try:
            tz = timezone.utc if name.upper() == "UTC" else ZoneInfo(name)
            label = name
        except (ZoneInfoNotFoundError, ValueError):
            tz, label = timezone.utc, "UTC"
        return {"iso": datetime.now(tz).isoformat(), "timezone": label}

    return {
        "get_current_time": CustomTool(
            execute=get_current_time,
            description="Return the current date/time, optionally for a given IANA timezone.",
            input_schema=_TIME_SCHEMA,
        )
    }


# --- Attaching an MCP server (example; not wired by default) --------------- #
# from cursor_sdk import HttpMcpServerConfig
#
# def build_mcp_servers() -> dict:
#     return {
#         "my-mcp": HttpMcpServerConfig(
#             url="https://my-host/mcp",
#             headers={"Authorization": "Bearer <token>"},
#         )
#     }
# Then pass `mcp_servers=build_mcp_servers()` into AgentOptions in cursor_agent.py.
