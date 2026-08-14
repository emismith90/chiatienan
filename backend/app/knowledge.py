"""What Phoenix knows, assembled for a human to look at and correct.

Three stores back the panel, and they are deliberately not one store (design D4,
D6, D7):

- **Places** — :class:`app.models.Place` rows, plus the counts
  :func:`app.places.stats` derives from the ledger.
- **Observations & standing rules** — ``observations.md``, one line per fact.
- **Conversation memory** — ``memory.md``, LLM roll-ups in dated sections.

This module is the read side (:func:`snapshot`) plus the label resolution that
makes the file stores legible: a line reading ``member:le-hoang-hung`` has to come
back as "Emi", or the person looking at it cannot tell whose note it is and so
cannot decide whether to delete it.

**Every derived number is read-only.** ``times``, ``days_since``, ``avg_per_head``
and ``band`` are computed here and rendered as text, never as fields: design D1 puts
those numbers in Python precisely so that nobody — model or human — hand-types
them.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app import memory, observations as obs_mod, places as places_mod, roster
from app.models import Member, Place


def _member_key(raw: str) -> str:
    """The ``member:`` suffix a name folds to (``"Le Hoang Hung"`` → ``"le-hoang-hung"``)."""
    return roster._fold(raw).replace(" ", "-")


class SubjectIndex:
    """Resolves an observation subject to the thing it is about.

    Two ``member:`` spellings are already in the wild and both must resolve to one
    person: the seed installer writes the **nickname** (``member:nhim``) and
    ``tools._memo_subject`` writes the folded **display name**
    (``member:le-hoang-hung``). Rather than rewriting anyone's file, every form is
    indexed and each line reports a canonical ``subject_key`` — so the panel groups
    both spellings under one heading, and a note written years apart by two
    different code paths still lands on the same person.
    """

    def __init__(self, session: Session, room_id: int):
        self.places: dict[str, Place] = {}
        for p in places_mod.list_places(session, room_id, include_inactive=True):
            self.places[p.slug] = p
            # Former slugs resolve too, and report the *current* one as the
            # canonical `subject_key`. `rename_slug` rewrites the live file, so a
            # line under an old slug can only come from a hand edit or a restored
            # backup — and rendering that as `⚠️ unknown` would hide which
            # restaurant it is from the one person who could fix it.
            for old in (p.former_slugs or []):
                self.places.setdefault(old, p)
        self.members: dict[str, Member] = {}
        # include_inactive: a note about someone who has since left the room must
        # still render their name. A note you cannot read is a note you cannot delete.
        for m in roster.list_members(session, room_id, include_inactive=True):
            for raw in [m.nickname, m.display_name, *(m.aliases or [])]:
                if (key := _member_key(raw or "")):
                    self.members.setdefault(key, m)

    def resolve(self, subject: str) -> dict:
        """``{"subject_key", "subject_kind", "subject_label", "subject_id"}``.

        ``subject_id`` is the row id, so a caller that already has a member or place
        in hand can find its notes without re-implementing the folding rules — which
        is the difference between "notes about this person" and "notes whose label
        happens to match this person's display name".

        An unresolvable subject comes back as ``kind="unknown"`` with the raw string
        as its label, never dropped: an orphaned note is exactly the note someone
        needs to see in order to clean it up.
        """
        prefix, _, rest = subject.partition(":")
        if prefix == "place":
            if (p := self.places.get(rest)) is not None:
                return {"subject_key": f"place:{p.slug}", "subject_kind": "place",
                        "subject_label": p.name, "subject_id": p.id}
        elif prefix == "member":
            if (m := self.members.get(rest)) is not None:
                return {"subject_key": f"member:{_member_key(m.nickname)}",
                        "subject_kind": "member", "subject_label": m.display_name,
                        "subject_id": m.id}
        return {"subject_key": subject, "subject_kind": "unknown",
                "subject_label": subject, "subject_id": None}

    def options(self) -> list[dict]:
        """Everything a new note can be filed against — the editor's picker.

        Active rows only: a note about a hidden place or a departed member can be
        read and deleted, but there is no reason to help anyone write a new one.
        """
        out = [{"subject": f"place:{p.slug}", "label": p.name, "kind": "place", "id": p.id}
               for p in self.places.values() if p.active]
        seen: set[int] = set()
        for m in self.members.values():
            if m.active and m.id not in seen:
                seen.add(m.id)
                out.append({"subject": f"member:{_member_key(m.nickname)}",
                            "label": m.display_name, "kind": "member", "id": m.id})
        return sorted(out, key=lambda o: (o["kind"], o["label"]))


def observation_rows(session: Session, room_id: int, *, today=None,
                     index: SubjectIndex | None = None) -> list[dict]:
    """Every parsed line of ``observations.md``, labelled and flagged.

    ``stale`` means the line is still in the file but past
    :data:`app.observations.DEFAULT_SINCE_DAYS`, so ``for_subjects`` no longer
    feeds it to the model. Shown rather than hidden — and never auto-deleted: a bot
    that prunes its own memory on a timer is a worse failure than a long file.
    """
    from app.clock import today_ict

    today = today or today_ict()
    index = index or SubjectIndex(session, room_id)
    cutoff = today - timedelta(days=obs_mod.DEFAULT_SINCE_DAYS)
    rows = []
    for o in obs_mod.load(room_id):
        rows.append({
            "id": o.line_id,
            "when": o.when.isoformat() if o.when else None,
            "standing": o.is_rule,
            "subject": o.subject,
            **index.resolve(o.subject),
            "gate": o.gate,
            "gate_kind": obs_mod.gate_kind(o),
            "gate_label": obs_mod.gate_label(o),
            "text": o.text,
            "stale": bool(o.when and o.when < cutoff),
        })
    # Rules first, then newest observations: a standing rule outranks a note about
    # one bad Tuesday, which is the same order `for_subjects` implies by never
    # ageing rules out. Two stable sorts, cheapest way to get "group, then date desc".
    rows.sort(key=lambda r: r["when"] or "", reverse=True)
    rows.sort(key=lambda r: not r["standing"])
    return rows


def _pending_memo_counts(session: Session, room_id: int) -> dict[str, int]:
    """``{subject: pending memo cards}`` for the room, in one pass.

    The rename confirmation has to say what will move, and a card frozen to the
    old slug is the half nobody expects. One query over the room's memo drafts
    rather than one per place — there are two of these in production, and there
    is no reason for the number of queries to track the number of restaurants.
    """
    from app.models import RoomMessage

    from sqlalchemy import select as _select
    out: dict[str, int] = {}
    rows = session.scalars(
        _select(RoomMessage).where(RoomMessage.room_id == room_id,
                                   RoomMessage.kind == "memo_draft")
    ).all()
    for m in rows:
        att = m.attachments or {}
        if att.get("status") != "pending":
            continue
        subject = att.get("subject")
        if subject:
            out[subject] = out.get(subject, 0) + 1
    return out


def place_rows(session: Session, room_id: int, *, today=None,
               note_counts: dict[str, int] | None = None) -> list[dict]:
    """Every place in the room — including hidden ones — with its ledger stats.

    Hidden (``active=False``) rows are included and flagged rather than filtered:
    hiding a place is how you delete one (meals reference it), so the panel is the
    only place anyone could ever bring one back.
    """
    from app.clock import today_ict

    today = today or today_ict()
    rows = places_mod.list_places(session, room_id, include_inactive=True)
    st = places_mod.stats(session, room_id, today=today)
    counts = note_counts or {}
    pending = _pending_memo_counts(session, room_id)
    out = []
    for p in rows:
        s = st.get(p.id, {})
        out.append({
            "id": p.id,
            "slug": p.slug,
            # Read-only, like the stats: appended by `rename_slug` and by nothing
            # else. Shown so the panel can explain why a stale seed file still
            # finds this row.
            "former_slugs": list(p.former_slugs or []),
            "name": p.name,
            "aliases": list(p.aliases or []),
            "tags": list(p.tags or []),
            "delivery": list(p.delivery or []),
            "address": p.address,
            "phone": p.phone,
            "walkable": p.walkable,
            "walk_minutes": p.walk_minutes,
            "price_hint": p.price_hint,
            "closed_until": p.closed_until.isoformat() if p.closed_until else None,
            "active": p.active,
            "untried": places_mod.UNTRIED_TAG in (p.tags or []),
            "note_count": counts.get(f"place:{p.slug}", 0),
            "pending_memo_count": pending.get(f"place:{p.slug}", 0),
            # Read-only, all of it (D1).
            "stats": {
                "times": s.get("times", 0),
                "last_on": s.get("last_on").isoformat() if s.get("last_on") else None,
                "days_since": s.get("days_since"),
                "avg_per_head": s.get("avg_per_head"),
                "band": s.get("band"),
                "weekday_counts": {str(k): v for k, v in (s.get("weekday_counts") or {}).items()},
            },
        })
    # Familiar places first — the ones with history are the ones worth correcting.
    out.sort(key=lambda r: (-r["stats"]["times"], r["name"]))
    return out


def memory_block(room_id: int) -> dict:
    meta = memory.read_meta(room_id)
    return {
        "sections": memory.parse_sections(room_id),
        "watermark": {
            "through_id": meta.get("summarized_through_id"),
            "through_at": meta.get("summarized_through_at"),
        },
    }


def snapshot(session: Session, room_id: int, *, today=None) -> dict:
    """The whole panel in one round-trip, mirroring ``GET .../ledger``.

    One fetch rather than three because the three tabs are always shown together
    and the payload is small; if a room's place list ever outgrows that, the
    caller narrows it with ``section=``.
    """
    index = SubjectIndex(session, room_id)
    obs_rows = observation_rows(session, room_id, today=today, index=index)
    counts: dict[str, int] = {}
    for r in obs_rows:
        counts[r["subject_key"]] = counts.get(r["subject_key"], 0) + 1
    return {
        "etags": {
            "observations": obs_mod.file_etag(room_id),
            "memory": memory.file_etag(room_id),
        },
        "places": place_rows(session, room_id, today=today, note_counts=counts),
        "observations": obs_rows,
        "memory": memory_block(room_id),
        "subjects": index.options(),
    }
