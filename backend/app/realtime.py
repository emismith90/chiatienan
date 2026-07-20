from __future__ import annotations
import asyncio
from collections import defaultdict


class RoomHub:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, room_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[room_id].add(q)
        return q

    def unsubscribe(self, room_id: int, q: asyncio.Queue) -> None:
        self._subs[room_id].discard(q)

    async def publish(self, room_id: int, event: dict) -> None:
        for q in list(self._subs.get(room_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # slow client: drop it and tell its stream to close → client reconnects with ?since=
                self._subs[room_id].discard(q)
                try:
                    q.get_nowait()          # make room
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait({"type": "__closed__"})
                except asyncio.QueueFull:
                    pass


hub = RoomHub()
