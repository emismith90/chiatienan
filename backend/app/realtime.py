from __future__ import annotations
import asyncio
from collections import defaultdict


class RoomHub:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)
        # How many bot turns are in flight per room. The typing / "bot is
        # working" indicators are driven by ephemeral events (bot.typing,
        # bot.done, agent.run.*) that are NOT persisted, so a client that
        # reconnects across the gap where a turn finished would miss the
        # terminal event and show a stuck indicator forever. Tracking busy-ness
        # lets stream() resync the indicator on every (re)connect.
        self._busy: dict[int, int] = defaultdict(int)

    def mark_busy(self, room_id: int) -> None:
        self._busy[room_id] += 1

    def mark_idle(self, room_id: int) -> None:
        if self._busy.get(room_id, 0) > 0:
            self._busy[room_id] -= 1

    def is_busy(self, room_id: int) -> bool:
        return self._busy.get(room_id, 0) > 0

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
