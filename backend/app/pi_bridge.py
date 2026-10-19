"""chiatienan's bridge to the Pi sidecar: a thin shim over ``kernos.engine.pi``.

The bridge itself moved to the framework (plan Task 1.4). What stays here is
everything host-specific: where the sidecar lives, what our credential is called
for ``PI_PROVIDER`` (``XAI_API_KEY`` for xai; ``OPEN_ROUTER_KEY``, not the
``OPENROUTER_API_KEY`` pi reads, for openrouter — the bridge translates), the
``PI_*`` defaults the child inherits from ``settings``, and the
one-per-process singleton ``chat._agent_lock`` and ``/internal/bridge-smoke``
share.
"""
from __future__ import annotations

from pathlib import Path

from app.config import PROVIDERS, provider, settings
from kernos.engine.pi.bridge import BridgeError  # noqa: F401  (re-exported)
from kernos.engine.pi.bridge import PiBridge as _PiBridge

SIDECAR_DIR = Path(__file__).resolve().parent.parent / "agent_sidecar"
SIDECAR_ENTRY = SIDECAR_DIR / "main.js"

#: The credential *we* use for the configured provider. Named here so a missing key
#: fails at spawn with the variable's real name rather than as an opaque provider
#: 401 mid-turn.
KEY_ENV = provider(settings.pi_provider)["key_env"]

#: The name **pi** reads it from. Where the two differ (openrouter) the boundary
#: translates: one canonical variable in our env and deploy config, mapped to pi's
#: expectation for the child process only.
PI_KEY_ENV = provider(settings.pi_provider)["pi_key_env"]

#: Never handed to the sidecar. The model there has a shell (pi's `bash` when a
#: profile enables it; Cursor's own on PI_PROVIDER=cursor, which no profile can
#: turn off), so anything in the child's environment is one prompt away from a room.
#: Neither our admin secrets nor another provider's key is needed by it.
CHILD_ENV_DENY = frozenset({"ADMIN_PASSWORD", "DEBUG_API_KEY"} | {
    name for row in PROVIDERS.values() for name in (row["key_env"], row["pi_key_env"])
} - {KEY_ENV, PI_KEY_ENV})


class PiBridge(_PiBridge):
    """The framework bridge, configured for this host."""

    def __init__(self, *, node: str = "node", entry: Path | None = None, env: dict | None = None):
        super().__init__(
            entry=entry or SIDECAR_ENTRY,
            node=node,
            cwd=SIDECAR_DIR,
            key_env=KEY_ENV,
            pi_key_env=PI_KEY_ENV,
            child_env_defaults={"PI_MODEL": settings.pi_model,
                                "PI_VISION_MODEL": settings.pi_vision_model,
                                "PI_PROVIDER": settings.pi_provider},
            # Not PI_CURSOR_SANDBOX=1: Cursor's sandbox does confine its shell's
            # writes, but its network block also cuts the loopback MCP bridge, so
            # every money tool call is refused and no turn can record anything.
            env=env,
        )

    def _child_env(self) -> dict:
        env = super()._child_env()
        for name in CHILD_ENV_DENY:
            env.pop(name, None)
        return env


#: One bridge per process. `chat._agent_lock` already serializes turns, and
#: `/internal/bridge-smoke` interleaves on purpose — which the `req_id`
#: demultiplexing is what makes safe.
_bridge: PiBridge | None = None


def get_bridge() -> PiBridge:
    global _bridge
    if _bridge is None:
        _bridge = PiBridge()
    return _bridge


async def close_bridge() -> None:
    global _bridge
    if _bridge is not None:
        await _bridge.aclose()
        _bridge = None
