"""The sidecar subprocess: framing, restart, and req_id demultiplexing.

Driven with a fake `node` that echoes canned JSONL, so no model and no key are
needed. The demux test is the load-bearing one: `/internal/bridge-smoke` is not
under `chat._agent_lock`, so a ping legitimately interleaves with a running turn.
"""
import asyncio
import json
import os
import sys
import textwrap

import pytest

from app import pi_bridge


def _fake_node(tmp_path, body: str):
    """A python script standing in for `node main.js`."""
    script = tmp_path / "fake_sidecar.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def _bridge(tmp_path, body, **env):
    entry = _fake_node(tmp_path, body)
    return pi_bridge.PiBridge(node=sys.executable, entry=entry,
                              env={pi_bridge.KEY_ENV: "test-key", **env})


ECHO_PONG = """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        command = json.loads(line)
        print(json.dumps({"type": "pong", "req_id": command["req_id"], "text": "pong"}),
              flush=True)
"""


async def test_a_ping_gets_its_reply(tmp_path):
    bridge = _bridge(tmp_path, ECHO_PONG)
    try:
        replies = [m async for m in bridge.request({"type": "ping", "req_id": "r1"})]
        assert replies == [{"type": "pong", "req_id": "r1", "text": "pong"}]
    finally:
        await bridge.aclose()


async def test_two_concurrent_requests_each_receive_only_their_own(tmp_path):
    # One stdout, two waiters. Without req_id demultiplexing a bridge-smoke ping
    # would steal a turn's reply.
    bridge = _bridge(tmp_path, """
        import json, sys, time
        for line in sys.stdin:
            command = json.loads(line)
            if command["req_id"] == "slow":
                time.sleep(0.15)
            print(json.dumps({"type": "pong", "req_id": command["req_id"],
                              "text": command["req_id"]}), flush=True)
    """)
    try:
        async def collect(req_id):
            return [m async for m in bridge.request({"type": "ping", "req_id": req_id})]
        slow, fast = await asyncio.gather(collect("slow"), collect("fast"))
        assert slow[0]["text"] == "slow"
        assert fast[0]["text"] == "fast"
    finally:
        await bridge.aclose()


async def test_a_missing_key_raises_at_spawn_naming_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv(pi_bridge.KEY_ENV, raising=False)
    bridge = pi_bridge.PiBridge(node=sys.executable,
                                entry=_fake_node(tmp_path, ECHO_PONG), env={})
    with pytest.raises(pi_bridge.BridgeError, match=pi_bridge.KEY_ENV):
        await bridge.ensure_started()


async def test_a_dead_child_is_restarted_on_the_next_request(tmp_path):
    bridge = _bridge(tmp_path, ECHO_PONG)
    try:
        await bridge.ensure_started()
        first = bridge._process
        first.kill()
        await first.wait()
        replies = [m async for m in bridge.request({"type": "ping", "req_id": "r2"})]
        assert replies[0]["type"] == "pong"
        assert bridge._process is not first
    finally:
        await bridge.aclose()


async def test_a_child_that_dies_mid_request_wakes_the_waiter(tmp_path):
    # A hung turn is worse than a failed one: chat.py would wait forever.
    bridge = _bridge(tmp_path, """
        import sys
        sys.stdin.readline()
        raise SystemExit(1)
    """)
    try:
        replies = [m async for m in bridge.request({"type": "run", "req_id": "r"})]
        assert replies and replies[-1]["type"] == "fatal"
    finally:
        await bridge.aclose()


async def test_a_spawn_failure_retries_then_reports(tmp_path):
    bridge = pi_bridge.PiBridge(node="/nonexistent/node",
                                entry=tmp_path / "x.js", env={pi_bridge.KEY_ENV: "k"})
    with pytest.raises(pi_bridge.BridgeError, match="failed to start"):
        await bridge.ensure_started()


async def test_an_unparseable_line_is_skipped_not_fatal(tmp_path):
    bridge = _bridge(tmp_path, """
        import json, sys
        command = json.loads(sys.stdin.readline())
        print("this is not json", flush=True)
        print(json.dumps({"type": "pong", "req_id": command["req_id"], "text": "ok"}), flush=True)
    """)
    try:
        replies = [m async for m in bridge.request({"type": "ping", "req_id": "r"})]
        assert replies[0]["text"] == "ok"
    finally:
        await bridge.aclose()


async def test_the_parent_environment_and_the_model_reach_the_child(tmp_path):
    bridge = _bridge(tmp_path, """
        import json, os, sys
        command = json.loads(sys.stdin.readline())
        print(json.dumps({"type": "pong", "req_id": command["req_id"],
                          "text": os.environ.get("PI_MODEL", "")}), flush=True)
    """)
    try:
        replies = [m async for m in bridge.request({"type": "ping", "req_id": "r"})]
        assert replies[0]["text"]      # settings.pi_model was passed through
    finally:
        await bridge.aclose()


async def test_a_node_child_behind_a_proxy_is_opted_into_it(tmp_path, monkeypatch):
    # Node's fetch ignores HTTPS_PROXY, so without this every outbound call fails
    # in a way that reads like a bad key.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    bridge = _bridge(tmp_path, ECHO_PONG)
    assert bridge._child_env()["NODE_USE_ENV_PROXY"] == "1"


def test_the_sidecar_entry_point_exists():
    assert pi_bridge.SIDECAR_ENTRY.is_file(), pi_bridge.SIDECAR_ENTRY


async def test_the_app_shutdown_hook_closes_the_sidecar(monkeypatch):
    # Without it the Node child outlives the event loop and a restarted backend
    # leaves an orphan holding a pipe nobody reads.
    from app import main
    closed = []
    monkeypatch.setattr("app.pi_bridge.close_bridge",
                        lambda: _record(closed))
    await main._stop_sidecar()
    assert closed == [True]


async def _record(sink):
    sink.append(True)


def test_our_key_is_translated_to_the_name_pi_reads(monkeypatch, tmp_path):
    # Two names for one secret on openrouter: we standardise on OPEN_ROUTER_KEY, pi
    # reads OPENROUTER_API_KEY (pi-ai/dist/env-api-keys.js:93). Without the mapping
    # the sidecar says "No API key found for openrouter. Use /login…", which reads
    # like a setup mistake rather than a naming mismatch.
    from app.config import PROVIDERS
    from kernos.engine.pi.bridge import PiBridge
    row = PROVIDERS["openrouter"]
    monkeypatch.setenv(row["key_env"], "sk-real")
    monkeypatch.delenv(row["pi_key_env"], raising=False)
    bridge = PiBridge(entry=tmp_path / "x.js", key_env=row["key_env"], pi_key_env=row["pi_key_env"])
    assert bridge._child_env()[row["pi_key_env"]] == "sk-real"


def test_the_default_provider_is_xai_and_the_sidecar_is_told_so(monkeypatch, tmp_path):
    # pi reads XAI_API_KEY itself, so nothing is translated. PI_PROVIDER must reach
    # the child: `grok-4.7` is listed under five providers in pi's catalogue, and the
    # sidecar would otherwise pick the first (github-copilot).
    assert (pi_bridge.KEY_ENV, pi_bridge.PI_KEY_ENV) == ("XAI_API_KEY", "XAI_API_KEY")
    monkeypatch.setenv("XAI_API_KEY", "xai-real")
    monkeypatch.delenv("PI_PROVIDER", raising=False)
    child = pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js", env={})._child_env()
    assert child["XAI_API_KEY"] == "xai-real" and child["PI_PROVIDER"] == "xai"


def test_an_unknown_provider_names_the_valid_ones():
    from app.config import provider
    with pytest.raises(ValueError, match="openrouter.*xai"):
        provider("grok")
