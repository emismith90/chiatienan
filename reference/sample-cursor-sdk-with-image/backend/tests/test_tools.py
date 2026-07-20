from app.tools import build_demo_tools


def test_demo_tools_has_get_current_time():
    tools = build_demo_tools()
    assert "get_current_time" in tools


def test_get_current_time_utc_returns_iso_with_offset():
    tools = build_demo_tools()
    result = tools["get_current_time"].execute({"timezone": "UTC"}, None)
    assert result["timezone"] == "UTC"
    assert result["iso"].endswith("+00:00")


def test_get_current_time_invalid_tz_falls_back_to_utc():
    tools = build_demo_tools()
    result = tools["get_current_time"].execute({"timezone": "Not/AZone"}, None)
    assert result["timezone"] == "UTC"
