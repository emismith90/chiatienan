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

from app.config import provider, settings
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
            env=env,
        )


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
