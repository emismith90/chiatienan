import asyncio
import pytest
from app.realtime import RoomHub


@pytest.mark.asyncio
async def test_publish_fans_out_to_room_only():
    h = RoomHub()
    a = h.subscribe(1); b = h.subscribe(1); other = h.subscribe(2)
    await h.publish(1, {"type": "message", "id": 5})
    assert (await asyncio.wait_for(a.get(), 1))["id"] == 5
    assert (await asyncio.wait_for(b.get(), 1))["id"] == 5
    assert other.empty()
    h.unsubscribe(1, a)


@pytest.mark.asyncio
async def test_publish_overflow_unsubscribes_and_closes_slow_client():
    h = RoomHub()
    slow = h.subscribe(1)
    fast = h.subscribe(1)

    # Fill the slow subscriber's queue to maxsize (100) without draining it.
    for i in range(100):
        await h.publish(1, {"type": "message", "id": i})

    # fast subscriber has been draining implicitly? No — fast is also never
    # drained, so both queues are full at this point. Drain fast so only
    # `slow` remains full for the next publish, isolating the overflow path.
    for _ in range(100):
        fast.get_nowait()

    # One more publish overflows `slow`'s queue.
    await h.publish(1, {"type": "message", "id": 100})

    # slow should have been unsubscribed from the room.
    assert slow not in h._subs[1]
    # fast should still be subscribed and receive the new event normally.
    assert (await asyncio.wait_for(fast.get(), 1))["id"] == 100

    # Drain slow's queue; it should end with a __closed__ sentinel event.
    events = []
    while not slow.empty():
        events.append(slow.get_nowait())
    assert events[-1] == {"type": "__closed__"}
