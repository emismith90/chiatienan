"""Test helpers for the notes store (it used to be ``observations.md``)."""
from app import observations as obs


def seed_notes(db, room_id: int, text: str) -> None:
    """Put the facts of ``observations.md``-format ``text`` into a room, in order."""
    facts, _report = obs.parse_text(text)
    with db.session() as s:
        for o in facts:
            obs.append(s, room_id, o)


def notes(db, room_id: int) -> list:
    with db.session() as s:
        return obs.load(s, room_id)


def note_lines(db, room_id: int) -> list[str]:
    """The room's facts rendered as they would have been written in the file."""
    return [o.to_line() for o in notes(db, room_id)]
