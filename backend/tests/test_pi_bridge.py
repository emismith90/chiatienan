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


def test_the_default_provider_is_cursor_and_the_sidecar_is_told_so(monkeypatch, tmp_path):
    # pi-cursor-sdk reads CURSOR_API_KEY itself, so nothing is translated.
    # PI_PROVIDER must reach the child: it is what loads the extension there, and
    # `grok-4.7` is listed under several providers in pi's catalogue — the sidecar
    # would otherwise pick the first (github-copilot).
    assert (pi_bridge.KEY_ENV, pi_bridge.PI_KEY_ENV) == ("CURSOR_API_KEY", "CURSOR_API_KEY")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-real")
    monkeypatch.delenv("PI_PROVIDER", raising=False)
    child = pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js", env={})._child_env()
    assert child["CURSOR_API_KEY"] == "cursor-real" and child["PI_PROVIDER"] == "cursor"


def test_the_sidecar_never_gets_our_admin_secrets_or_other_providers_keys(monkeypatch, tmp_path):
    # The model there has a shell (Cursor's own cannot be turned off), so anything in
    # the child's environment is one prompt away from a room.
    for name in ("ADMIN_PASSWORD", "DEBUG_API_KEY", "XAI_API_KEY", "OPEN_ROUTER_KEY",
                 "OPENROUTER_API_KEY", "CURSOR_API_KEY"):
        monkeypatch.setenv(name, f"secret-{name}")
    child = pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js", env={})._child_env()
    assert child[pi_bridge.KEY_ENV] == f"secret-{pi_bridge.KEY_ENV}"
    leaked = {n for n in ("ADMIN_PASSWORD", "DEBUG_API_KEY", "XAI_API_KEY", "OPEN_ROUTER_KEY",
                          "OPENROUTER_API_KEY") if n in child}
    assert not leaked


def test_an_empty_default_variable_is_filled_not_kept(monkeypatch, tmp_path):
    # Settings reads PI_PROVIDER="" as the default provider; the sidecar must agree,
    # or it loads no Cursor extension and finds no model.
    monkeypatch.setenv("PI_PROVIDER", "")
    child = pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js", env={})._child_env()
    assert child["PI_PROVIDER"] == "cursor"


def test_an_unknown_provider_names_the_valid_ones():
    from app.config import provider
    with pytest.raises(ValueError, match="cursor.*openrouter.*xai"):
        provider("grok")


def _with_sidecar_user(monkeypatch, tmp_path, user: str):
    import dataclasses
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    monkeypatch.setattr(pi_bridge, "settings", dataclasses.replace(
        pi_bridge.settings, pi_sidecar_user=user, data_dir=str(data)))
    return data


def test_without_a_sidecar_user_the_child_runs_as_the_backend(tmp_path):
    assert pi_bridge.settings.pi_sidecar_user == ""
    assert pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js")._spawn_options() == {}


def test_a_sidecar_user_gets_its_own_home_and_data_dir_is_closed_to_it(monkeypatch, tmp_path):
    # The model's shell inherits the sidecar's user: DATA_DIR must not be
    # traversable by it, and HOME must be its own (Cursor writes ~/.cursor).
    import pwd
    me = pwd.getpwuid(os.getuid())
    data = _with_sidecar_user(monkeypatch, tmp_path, me.pw_name)
    monkeypatch.setattr(pi_bridge.os, "geteuid", lambda: 0)
    bridge = pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js",
                                env={pi_bridge.KEY_ENV: "k"})
    assert bridge._spawn_options() == {"user": me.pw_uid, "group": me.pw_gid, "extra_groups": []}
    assert data.stat().st_mode & 0o777 == 0o700
    assert bridge._child_env()["HOME"] == me.pw_dir


def test_a_sidecar_user_needs_a_root_backend(monkeypatch, tmp_path):
    # Silently running the shell as the backend's own user would be the unconfined
    # setup the operator asked to leave.
    import pwd
    _with_sidecar_user(monkeypatch, tmp_path, pwd.getpwuid(os.getuid()).pw_name)
    monkeypatch.setattr(pi_bridge.os, "geteuid", lambda: 1000)
    with pytest.raises(pi_bridge.BridgeError, match="root"):
        pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js")._spawn_options()


def test_an_unknown_sidecar_user_is_named(monkeypatch, tmp_path):
    _with_sidecar_user(monkeypatch, tmp_path, "no-such-user-here")
    with pytest.raises(pi_bridge.BridgeError, match="no-such-user-here"):
        pi_bridge.PiBridge(node="python3", entry=tmp_path / "x.js")._spawn_options()


@pytest.mark.skipif(os.geteuid() != 0, reason="dropping to another user needs root")
async def test_the_dropped_sidecar_cannot_reach_data_dir(monkeypatch):
    # End to end: the child really runs as the other user, and the ledger's
    # directory is closed to it while the rest of the tree is not. pytest's own
    # tmp dirs are root-only, so this builds a tree "nobody" can walk.
    import pwd
    import shutil
    import tempfile
    from pathlib import Path
    base = Path(tempfile.mkdtemp())
    try:
        os.chmod(base, 0o755)
        data = _with_sidecar_user(monkeypatch, base, "nobody")
        (data / "chiatienan.db").write_text("ledger")
        (base / "public.txt").write_text("public")
        entry = _fake_node(base, """
            import json, os, sys
            def can_read(path):
                try:
                    open(path).read()
                    return "read"
                except PermissionError:
                    return "denied"
            for line in sys.stdin:
                command = json.loads(line)
                text = " ".join([str(os.getuid()), can_read(command["public"]),
                                 can_read(command["db"])])
                print(json.dumps({"type": "pong", "req_id": command["req_id"],
                                  "text": text}), flush=True)
        """)
        bridge = pi_bridge.PiBridge(node=os.path.realpath(sys.executable), entry=entry,
                                    env={pi_bridge.KEY_ENV: "test-key"})
        try:
            reply = [m async for m in bridge.request({
                "type": "ping", "req_id": "r1", "public": str(base / "public.txt"),
                "db": str(data / "chiatienan.db")})]
            assert reply[0]["text"] == f"{pwd.getpwnam('nobody').pw_uid} read denied"
        finally:
            await bridge.aclose()
    finally:
        shutil.rmtree(base)
