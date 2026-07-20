import inspect
from app.cursor_agent import run_agent_cursor


def test_run_agent_cursor_is_async_generator_with_expected_signature():
    assert inspect.isasyncgenfunction(run_agent_cursor)
    params = inspect.signature(run_agent_cursor).parameters
    assert "model_override" in params and "images" in params
    assert "run_input" in params
    # decoupled: no Atlas-era params
    for gone in ("user_email", "user_role", "page_filter", "credential_headers", "graph_mode"):
        assert gone not in params
