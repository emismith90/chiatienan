"""``lunch_places``: the restaurant/knowledge tools as a pack (plan Task 3.1, 3.3).

Stays in the host through Phase 3 because it is welded to the host's memory files
and knowledge panel (decision 5); the framework gets a home for it in Phase 5. The
tool bodies are those of ``app/tools.py`` before Task 3.3, unchanged.
"""
from __future__ import annotations

import random

from sqlalchemy import select

from app import roster
from app.models import Place
from kernos.packs import BasePack, PackTool, err as _err
from packs.lunch_ledger.tools import _parse_iso

PLACES_TOOLS = frozenset({"find_places", "suggest_lunch", "remember", "forget", "add_place"})

_FIND_PLACES_SCHEMA = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array", "items": {"type": "string"},
            "description": "Place names as the user wrote them ('thịnh lơ', 'quán bé bự').",
        },
        "all": {"type": "boolean", "description": "Return every place in the room."},
    },
}

_ADD_PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "aliases": {
            "type": "array", "items": {"type": "string"},
            "description": "Other spellings the group uses, including tone-free forms.",
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "delivery": {
            "type": "array", "items": {"type": "string"},
            "description": "Ordering apps, e.g. ['shopeefood', 'grab'].",
        },
        "address": {"type": "string"},
        "phone": {"type": "string"},
        "walkable": {"type": "boolean",
                     "description": "Can the group walk there from the office?"},
    },
    "required": ["name"],
}

_SUGGEST_LUNCH_SCHEMA = {
    "type": "object",
    "properties": {
        "budget": {"type": "string", "enum": ["rẻ", "vừa", "đắt"],
                   "description": "Only places in this price band."},
        "delivery": {"type": "boolean",
                     "description": "True when the group wants to order in rather than walk out."},
        "exclude": {"type": "array", "items": {"type": "string"},
                    "description": "Places to leave out ('vừa ăn hôm qua rồi')."},
        "today": {"type": "string", "description": "YYYY-MM-DD; omit for today."},
    },
}

_REMEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "about": {"type": "string",
                  "description": "Quán hoặc người mà ghi nhớ này nói về ('Bé Bự', 'Nhím')."},
        "text": {"type": "string", "description": "Nội dung, tiếng Việt, một câu."},
        "standing": {"type": "boolean",
                     "description": "true = luật lâu dài ('phải đặt trước'), false = chuyện hôm nay."},
        "gate": {"type": "string",
                 "description": "Luật theo giờ: busy@HH:MM, order-by@HH:MM, closes@HH:MM."},
    },
    "required": ["about", "text"],
}

_FORGET_SCHEMA = {
    "type": "object",
    "properties": {
        "about": {"type": "string"},
        "text": {"type": "string", "description": "Nội dung ghi nhớ cần xoá, đúng nguyên văn."},
    },
    "required": ["about", "text"],
}


def build(ctx) -> dict[str, PackTool]:
    db = ctx.db

    def find_places(args, _tool_ctx=None) -> dict:
        args = args or {}
        from app import places as places_mod

        with db.session() as s:
            if args.get("all"):
                rows = places_mod.list_places(s, ctx.space_id)
                return {"ok": True, "places": [
                    {"id": p.id, "name": p.name, "slug": p.slug, "tags": p.tags,
                     "walkable": p.walkable} for p in rows
                ]}
            res = places_mod.resolve(
                s, ctx.space_id, names=[str(n) for n in args.get("names") or []]
            )
        return {"ok": True, **res}

    def suggest_lunch(args, _tool_ctx=None) -> dict:
        """Rank where the group should eat. **The tool decides the order.**

        Every number behind the ranking — how long since, how often, how
        expensive — is computed in Python (design D1). The model gets a decided
        list and writes prose around it; it must never re-rank, and it never
        sees a VND amount, only a band (D5), so a suggestion can never be
        mistaken for a ledger figure.
        """
        args = args or {}
        from app import places as places_mod

        want_delivery = bool(args.get("delivery"))
        budget = (args.get("budget") or "").strip() or None
        exclude_raw = [str(x) for x in args.get("exclude") or []]
        today = _parse_iso(args.get("today")) or ctx.today()

        with db.session() as s:
            rows = places_mod.list_places(s, ctx.space_id)
            counts = places_mod.stats(s, ctx.space_id, today=today)
            excluded_ids = {
                p.id for raw in exclude_raw
                for p in [places_mod.resolve_best(s, ctx.space_id, raw, today=today)[0]]
                if p is not None
            }

            pool = []
            for p in rows:
                # A temporary closure expires on its own (D11) — no cleanup job.
                if p.closed_until and p.closed_until >= today:
                    continue
                # Going out and ordering in are different questions (D16).
                if want_delivery:
                    if not p.delivery:
                        continue
                elif not p.walkable:
                    continue
                if p.id in excluded_ids:
                    continue
                st = counts.get(p.id, {})
                # An unknown band cannot be ruled out by a budget.
                if budget and st.get("band") and st["band"] != budget:
                    continue
                pool.append((p, st))

            def score(item) -> float:
                """Familiarity first, minus a short-lived just-ate-there penalty.

                An earlier version scored purely on days-since, which inverted the
                whole feature: a favourite eaten every fortnight ranked *below* a
                place nobody had been to, so the room's usuals would essentially
                never be suggested. Frequency is the positive signal; recency is
                only a penalty, and only for a few days.
                """
                p, st = item
                days = st.get("days_since")
                # Caps at 8 visits so one much-loved place cannot crowd out the
                # rest of the rotation forever.
                familiarity = min(st.get("times", 0), 8) * 6.0
                # 30 the day you ate there, tapering to 0 by day 10.
                recent_penalty = 0.0 if days is None else max(0.0, 30.0 - days * 3.0)
                weekday = (st.get("weekday_counts") or {}).get(today.weekday(), 0) * 6.0
                # A small nudge so never-eaten places surface sometimes...
                novelty = 8.0 if days is None else 0.0
                # ...but a directory import stays well behind the real favourites (D14).
                untried = 25.0 if places_mod.UNTRIED_TAG in (p.tags or []) else 0.0
                # Jitter so the same place does not lead every single day. Without
                # it 40-odd never-eaten places tie exactly and stable sort falls
                # back to alphabetical — "Bánh Mì Linh" forever. The TOOL decides,
                # never the model (same rule as pick_random).
                return (familiarity + weekday + novelty
                        - recent_penalty - untried + random.uniform(0.0, 5.0))

            pool.sort(key=score, reverse=True)

            # Prose and clock rules for these candidates only — the file could
            # grow for years and the turn stays small.
            from app import observations as obs_mod
            from app.clock import now_ict

            now = now_ict()
            notes = obs_mod.for_subjects(
                ctx.space_id, [f"place:{p.slug}" for p, _ in pool], today=today)
            by_subject: dict[str, list] = {}
            for o in notes:
                by_subject.setdefault(o.subject, []).append(o)

            candidates = []
            for p, st in pool:
                mine = by_subject.get(f"place:{p.slug}", [])
                status, minutes_left, kind, gate_note = "ok", None, None, None
                for o in mine:
                    if not o.gate:
                        continue
                    # Worst status wins: one shut door beats three fine ones.
                    s_, left = obs_mod.gate_status(o, now=now, walk_minutes=p.walk_minutes)
                    if s_ == "too_late" or (s_ == "act_now" and status == "ok"):
                        status, minutes_left = s_, left
                        kind, gate_note = obs_mod.gate_kind(o), o.text
                candidates.append({
                    "place_id": p.id,
                    "name": p.name,
                    "band": st.get("band"),
                    "days_since": st.get("days_since"),
                    "times": st.get("times", 0),
                    "phone": p.phone,
                    "tags": [t for t in (p.tags or []) if t != places_mod.UNTRIED_TAG],
                    "untried": places_mod.UNTRIED_TAG in (p.tags or []),
                    "status": status,
                    "gate_kind": kind,
                    "minutes_left": minutes_left,
                    # The note the gate came from, so an explanation quotes the
                    # actual reason rather than whichever note happened to be
                    # first ("ăn được, mới sửa quán" is not why it is too late).
                    "gate_note": gate_note,
                    "notes": [o.text for o in mine],
                })

            # A place you cannot get to is not a weak suggestion, it is a wrong
            # one — but it still ships with its reason, so Phoenix can say why.
            candidates.sort(key=lambda c: c["status"] == "too_late")
        return {"ok": True, "mode": "delivery" if want_delivery else "walk",
                "candidates": candidates}

    def _memo_subject(s, raw: str) -> tuple[str, str] | None:
        """Resolve free text to ``("place:slug"|"member:nick", label)``, or None.

        **A place only wins on a CONFIDENT tier, never a tie-break.** "Bún riêu cô
        Trang" is a full restaurant name and matches exactly; a bare "cô Trang" is
        a person in this room — two of them — and `resolve_best` would happily
        tie-break it onto the restaurant, filing a note about a colleague against
        a bún riêu shop. Requiring an exact/folded/prefix hit means the bare form
        falls through to the roster where it belongs.

        If the text plausibly names a person *as well*, refuse and let the model
        ask. Across namespaces, ambiguity is never guessed (design D18).
        """
        from app import places as places_mod

        place, tier = places_mod.resolve_one(s, ctx.space_id, raw)
        res = roster.resolve(s, ctx.space_id, names=[raw])
        person_plausible = bool(res["matched"] or res["ambiguous"])

        if place is not None and tier in places_mod.CONFIDENT_TIERS and not person_plausible:
            return f"place:{place.slug}", place.name
        if len(res["matched"]) == 1 and place is None:
            m = res["matched"][0]
            return f"member:{roster._fold(m['display_name']).replace(' ', '-')}", m["display_name"]
        return None

    def remember(args, _tool_ctx=None) -> dict:
        """Propose remembering a fact about a place or a person.

        Proposal, not a write: an observation asserts something about a person or
        a business, and TODO.md's "no way to verify a false claim" applies (D7).
        """
        args = args or {}
        from app import memos

        raw = (args.get("about") or "").strip()
        text = (args.get("text") or "").strip()
        if not raw or not text:
            return _err("Cần biết ghi nhớ VỀ AI/QUÁN NÀO và NỘI DUNG gì.")
        gate = (args.get("gate") or "").strip() or None
        when = None if args.get("standing") else ctx.today()
        with db.session() as s:
            found = _memo_subject(s, raw)
            if found is None:
                return _err(
                    f"Không rõ «{raw}» là quán nào hay ai. Gọi `find_places` hoặc "
                    "`find_members` để xác định trước, hoặc `add_place` nếu là quán mới."
                )
            subject, label = found
            try:
                m = memos.create(s, ctx.space_id, action="add", subject=subject,
                                 subject_label=label, text=text, when=when, gate=gate)
            except memos.MemoError as exc:
                return _err(str(exc))
            return {"ok": True, "type": "memo_draft", "memo_id": m.id,
                    "subject": subject, "subject_label": label, "text": text}

    def forget(args, _tool_ctx=None) -> dict:
        """Propose deleting a remembered fact. Confirmed on a card, like adding."""
        args = args or {}
        from app import memos, observations as obs_mod

        raw = (args.get("about") or "").strip()
        text = (args.get("text") or "").strip()
        if not raw or not text:
            return _err("Cần biết xoá ghi nhớ VỀ AI/QUÁN NÀO và NỘI DUNG gì.")
        with db.session() as s:
            found = _memo_subject(s, raw)
            if found is None:
                return _err(f"Không rõ «{raw}» là quán nào hay ai.")
            subject, label = found
            existing = [o for o in obs_mod.load(ctx.space_id) if o.subject == subject]
            if not any(o.text == text for o in existing):
                return _err(
                    f"Không có ghi nhớ nào của «{label}» khớp đúng nội dung đó. "
                    f"Hiện có: {[o.text for o in existing] or 'chưa có gì'}."
                )
            m = memos.create(s, ctx.space_id, action="remove", subject=subject,
                             subject_label=label, text=text)
            return {"ok": True, "type": "memo_draft", "memo_id": m.id,
                    "subject": subject, "subject_label": label, "text": text}

    def add_place(args, _tool_ctx=None) -> dict:
        """Create a restaurant row. Writes immediately, like ``add_member``.

        A place is inert until someone eats there (design D7) — nothing about it
        asserts anything, so it needs no confirm card. Observations do.
        """
        args = args or {}
        from app import places as places_mod

        name = (args.get("name") or "").strip()
        if not name:
            return _err("Missing place name.")
        slug = places_mod.slugify(name)
        with db.session() as s:
            existing = s.scalars(
                select(Place).where(Place.room_id == ctx.space_id, Place.slug == slug)
            ).first()
            if existing is not None:
                return {"ok": True, "place_id": existing.id, "slug": existing.slug,
                        "name": existing.name, "already_existed": True}
            try:
                p = places_mod.create_place(
                    s, ctx.space_id, name=name,
                    aliases=args.get("aliases") or [],
                    tags=args.get("tags") or [],
                    delivery=args.get("delivery") or [],
                    address=args.get("address"), phone=args.get("phone"),
                    walkable=bool(args.get("walkable", True)),
                )
            except places_mod.PlaceError as exc:
                return _err(str(exc))
            return {"ok": True, "place_id": p.id, "slug": p.slug, "name": p.name,
                    "already_existed": False}

    specs = {
        "find_places": dict(
            execute=find_places,
            description=(
                "Look up restaurants the group knows by name ('thịnh lơ', 'quán bé bự'), "
                "or list them all with all:true. Returns places, never people — use "
                "`find_members` for people."
            ),
            input_schema=_FIND_PLACES_SCHEMA,
        ),
        "suggest_lunch": dict(
            execute=suggest_lunch,
            description=(
                "Decide where the group should eat ('trưa nay ăn gì?', 'ăn gì bây giờ'). "
                "The TOOL ranks — do not re-order, do not pick a different one. Returns a "
                "price band (rẻ/vừa/đắt), never an amount. Pass delivery:true when they "
                "want to order in rather than walk out."
            ),
            input_schema=_SUGGEST_LUNCH_SCHEMA,
        ),
        "remember": dict(
            execute=remember,
            description=(
                "Đề xuất ghi nhớ một điều về quán hoặc về một người ('quán này hay hết gà', "
                "'Giang thích bún riêu', 'phải gọi trước 11h30'). Tạo THẺ để người dùng xác "
                "nhận — không ghi thẳng. Dùng standing:true cho luật lâu dài."
            ),
            input_schema=_REMEMBER_SCHEMA,
        ),
        "forget": dict(
            execute=forget,
            description=(
                "Đề xuất xoá một ghi nhớ đã có ('quán đó cải thiện rồi, bỏ ghi chú kia đi'). "
                "Cũng cần xác nhận trên thẻ."
            ),
            input_schema=_FORGET_SCHEMA,
        ),
        "add_place": dict(
            execute=add_place,
            description=(
                "Add a restaurant the group has started going to. Writes immediately "
                "(a place row is inert until someone eats there). Seed `aliases` with "
                "every spelling the group actually types, including tone-free ones."
            ),
            input_schema=_ADD_PLACE_SCHEMA,
        ),
    }

    return {name: PackTool(name, spec["description"], spec["input_schema"], spec["execute"])
            for name, spec in specs.items()}


class LunchPlacesPack(BasePack):
    id, version, handles_money = "lunch_places", "1", False

    def tools(self, ctx) -> dict[str, PackTool]:
        return build(ctx)

    # `seed()` stays the no-op default: `seed_places.load_file` needs a seed file path,
    # which is a deployment decision, not a pack default. Phase 5 decides where it lives.
