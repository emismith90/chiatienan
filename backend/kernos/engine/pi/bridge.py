"""Subprocess and framing for the Pi sidecar. Nothing else.

This file owns a pipe, not a policy. It spawns the sidecar entry, writes JSONL,
reads JSONL, and demultiplexes replies by ``req_id``. It knows the message
``type`` strings and nothing else about how pi works.

**Operational test for the boundary:** if this module starts branching on pi
semantics — event shapes, model names, answer assembly — that logic belongs in
`turn.js`. The whole point of the split is that Python reimplements no part of
pi's behavior (Pi design §3.1).

The sidecar is long-lived: one process serves every turn, so the spawn cost is
paid once. Sessions are per-turn, inside Node.

Moved from ``app/pi_bridge.py`` (plan Task 1.4). Everything the host used to
supply through ``app.config.settings`` and module constants — the entry path,
the two key-variable names, the ``PI_*`` defaults for the child — is a
constructor parameter now, so the framework has no host knowledge.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("kernos.engine.pi")

#: Retries and backoff for a child that dies during startup.
_LAUNCH_RETRIES = 3
_LAUNCH_BACKOFF = 1.5

#: The name **pi** reads the key from (`pi-ai/dist/env-api-keys.js:87`).
DEFAULT_PI_KEY_ENV = "OPENROUTER_API_KEY"


class BridgeError(RuntimeError):
    """The sidecar could not be started or died mid-request."""


class PiBridge:
    """One long-lived sidecar process, with `req_id`-demultiplexed replies."""

    def __init__(self, *, entry: Path | str, node: str = "node", cwd: Path | str | None = None,
                 key_env: str = DEFAULT_PI_KEY_ENV, pi_key_env: str = DEFAULT_PI_KEY_ENV,
                 child_env_defaults: dict | None = None, env: dict | None = None):
        self._node = node
        self._entry = Path(entry)
        self._cwd = Path(cwd) if cwd is not None else self._entry.parent
        self._key_env = key_env
        self._pi_key_env = pi_key_env
        self._child_env_defaults = dict(child_env_defaults or {})
        self._env_overrides = env or {}
        self._process: asyncio.subprocess.Process | None = None
        self._queues: dict[str, asyncio.Queue] = {}
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def entry(self) -> Path:
        return self._entry

    # ---------------------------------------------------------------- lifecycle

    def _child_env(self) -> dict:
        env = dict(os.environ)
        if not env.get(self._key_env):
            raise BridgeError(
                f"{self._key_env} is not set — the sidecar cannot reach the provider. "
                "See .env.example."
            )
        env[self._pi_key_env] = env[self._key_env]
        for name, value in self._child_env_defaults.items():
            if value:
                env.setdefault(name, value)
        # A Node child does not honour HTTPS_PROXY on its own: `fetch`/undici
        # ignores it, so behind a proxy every outbound call fails in a way that
        # reads like a bad key. Opt the child in explicitly when a proxy is set.
        if env.get("HTTPS_PROXY") or env.get("https_proxy"):
            env.setdefault("NODE_USE_ENV_PROXY", "1")
        env.update(self._env_overrides)
        return env

    async def ensure_started(self) -> None:
        """Start the sidecar if it is not running. Safe to call every turn."""
        async with self._lock:
            if self._process is not None and self._process.returncode is None:
                return
            if self._process is not None:
                logger.warning("[pi] sidecar exited with %s — restarting",
                               self._process.returncode)
                self._teardown()

            last: Exception | None = None
            for attempt in range(1, _LAUNCH_RETRIES + 1):
                try:
                    self._process = await asyncio.create_subprocess_exec(
                        self._node, str(self._entry),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=str(self._cwd),
                        env=self._child_env(),
                        limit=4 * 1024 * 1024,   # a bill photo's base64 is large
                    )
                    self._reader = asyncio.create_task(self._read_loop())
                    return
                except BridgeError:
                    raise                        # a missing key is not transient
                except Exception as exc:         # noqa: BLE001 — transient spawn flake
                    last = exc
                    logger.warning("[pi] spawn attempt %d/%d failed: %s",
                                   attempt, _LAUNCH_RETRIES, exc)
                    if attempt < _LAUNCH_RETRIES:
                        await asyncio.sleep(_LAUNCH_BACKOFF ** attempt)
            raise BridgeError(f"sidecar failed to start after {_LAUNCH_RETRIES} attempts: {last}")

    def _teardown(self) -> None:
        if self._reader:
            self._reader.cancel()
        self._reader = None
        self._process = None
        # Anything still waiting will never be answered; say so rather than hang.
        for queue in self._queues.values():
            queue.put_nowait({"type": "fatal", "message": "sidecar exited"})
        self._queues.clear()

    async def aclose(self) -> None:
        process, self._process = self._process, None
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
        self._queues.clear()

    # ------------------------------------------------------------------- pipes

    async def _read_loop(self) -> None:
        """Fan every line out to the queue for its `req_id`."""
        assert self._process and self._process.stdout
        stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("[pi] unparseable line from sidecar: %s", text[:200])
                    continue
                queue = self._queues.get(message.get("req_id"))
                if queue is not None:
                    queue.put_nowait(message)
                elif message.get("type") == "fatal":
                    logger.error("[pi] fatal with no waiter: %s", message.get("message"))
        except asyncio.CancelledError:
            raise
        finally:
            stderr_task.cancel()
            # Waking every waiter is the difference between a failed turn and a
            # hung one.
            for queue in self._queues.values():
                queue.put_nowait({"type": "fatal", "message": "sidecar stdout closed"})

    async def _drain_stderr(self) -> None:
        """Sidecar stderr is the only place a Node stack trace can go."""
        assert self._process and self._process.stderr
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            logger.warning("[pi:stderr] %s", line.decode("utf-8", "replace").rstrip())

    async def send(self, message: dict) -> None:
        """Write one command. Does not wait for a reply."""
        await self.ensure_started()
        assert self._process and self._process.stdin
        self._process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self._process.stdin.drain()

    async def request(self, command: dict):
        """Send `command` and yield every reply carrying its `req_id`.

        The caller decides when it has what it needs and stops iterating — a
        `run` ends on `turn_done`, a `ping` on `pong`.
        """
        req_id = command["req_id"]
        # Start (or restart) *before* registering: a dead child's read loop wakes
        # every queue it can see with a `fatal`, and if this request's queue were
        # already registered the restart would hand it the previous process's
        # death instead of its own reply.
        await self.ensure_started()
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[req_id] = queue
        try:
            await self.send(command)
            while True:
                message = await queue.get()
                yield message
                if message.get("type") in ("turn_done", "summarize_done", "pong", "fatal"):
                    return
        finally:
            self._queues.pop(req_id, None)
