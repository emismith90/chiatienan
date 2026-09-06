"""``StoreTraces``: the :class:`kernos.adapters.TraceStore` over the content store's
``kn_turn_traces`` table — what a host that already runs the content plane uses."""
from __future__ import annotations

from typing import Any

from kernos.content.store import ContentStore


class StoreTraces:
    def __init__(self, store: ContentStore) -> None:
        self._store = store

    def write(self, space_id, turn_id, *, started, finished, summary, tools, trace, keep_days=None) -> dict:
        return self._store.write_trace(space_id, turn_id, started=started, finished=finished,
                                       summary=summary, tools=tools, trace=trace, keep_days=keep_days)

    def list(self, space_id, *, limit: int = 50) -> list[dict]:
        return self._store.list_traces(space_id, limit=limit)

    def get(self, space_id, ref: Any) -> dict | None:
        return self._store.get_trace(space_id, ref)
