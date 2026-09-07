"""chiatienan's ``ModelProbe``: run ``bench.probe_models`` against a model id.

``bench`` is a dev-only package (not in the production image, and it imports
``app``), so it is imported inside the method (review finding 9); without it, or
without the provider key, the probe reports "not available" and the admin route
answers 501. The probe sends the **live** tool schemas, so a passing record means
the model emitted well-formed calls against exactly what the tools validate.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone


class BenchModelProbe:
    async def probe(self, model_id: str) -> dict:
        try:
            from bench import probe_models
            from bench.judge import KEY_ENV
        except ImportError as exc:
            raise NotImplementedError("bench.probe_models is not available on this host") from exc
        key = os.environ.get(KEY_ENV)
        if not key:
            raise NotImplementedError(f"{KEY_ENV} is not set; cannot probe")
        schemas = probe_models._tool_schemas()
        rows = await asyncio.get_running_loop().run_in_executor(
            None, lambda: probe_models.probe(model_id, key, schemas))
        return {
            "ok": bool(rows) and all(r.get("ok") for r in rows),
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schemas": [r.get("tool") for r in rows],
            "source": "bench.probe_models via admin API",
            "results": rows,
        }
