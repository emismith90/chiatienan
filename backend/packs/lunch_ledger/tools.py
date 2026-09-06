"""The lunch ledger's LLM-facing tools — where every number lives (design D3).

The model decides *when* to call these; the tools own all arithmetic and all
QR-building. Each tool opens its own short-lived DB session, so a turn that fails
before ``settle_period`` returns never half-writes. ``propose_meal`` never writes
at all — it only returns a draft payload for the user to confirm; the
deterministic commit happens elsewhere via ``ledger_core.drafts``. Validation
failures are returned as ``{"ok": False, "error": ...}`` dicts (a clarifying
question) rather than raised, so the model can ask the user instead of guessing.

Numbers that end up in a QR are computed and rendered entirely inside
``settle_period`` — they never round-trip tool → LLM → tool.

What the pack needs from the host's per-turn context (``ctx``) — duck-typed, so
this package never imports a host:

``db.session()``          a SQLAlchemy session context manager
``space_id``              the room/table/group the turn is confined to
``sender_member_id``      who is talking (a member id in that space)
``turn_mentions``         people @mentioned in this message
``unknown_names``         names this turn looked up and never pinned down (mutated)
``cards``                 ``kernos.adapters.CardStore`` — pending drafts, cancel
``today()``               the host clock's local date
``choice(seq)``           the host's uniform draw (``random.choice`` in production)

Bodies are those of chiatienan's ``app/tools.py`` before plan Task 3.3, with the
host couplings replaced by the injection points above.
"""
from __future__ import annotations

from datetime import date

from kernos.packs import PackTool, err as _err
from ledger_core import ledger, roster
from ledger_core.money import MoneyError, itemized_adjustments, normalize_items, prorate_items, split_with_guests
from ledger_core.periods import resolve_date


def _parse_iso(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _dropped_names(ctx, db, participants: list[int],
                   payer: int | None, guests: list[str]) -> list[str]:
    """Names the turn looked up, never pinned down, and never accounted for.

    WHY — production, 2026-08-13: *"nay ăn bún cá với anh Hưng chị Nhím hết
    175k"*. ``find_members`` matched Nhím and missed Hưng, the model called him a
    guest **in its prose** and then proposed the meal without a ``guests``
    entry. Two heads instead of three: every share on that card was 50% too big,
    and nothing on it said a person had gone missing. A name is "accounted for"
    once one of its words shows up in a participant/payer's name or in a guest
    label — so resolving him on a second lookup, adding him as a member, or
    listing him as a guest all clear it. Anything left is a person the split
    silently forgot.
    """
    if not ctx.unknown_names:
        return []
    with db.session() as s:
        tokens_by_id = roster.member_name_tokens(s, ctx.space_id)
    accounted: set[str] = set()
    for mid in [*participants, *([payer] if payer else [])]:
        accounted |= tokens_by_id.get(mid, set())
    for g in guests:
        accounted |= roster.name_tokens(g)
    return [raw for raw, _why in ctx.unknown_names.items()
            if not (roster.name_tokens(raw) & accounted)]


def _names_for(session, space_id, ids) -> dict[int, str]:
    # include_inactive: a balance/settlement can reference a since-removed member.
    return {
        m.id: m.display_name
        for m in roster.list_members(session, space_id, include_inactive=True)
        if m.id in set(ids)
    }



# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "payer": {"type": "integer", "description": "member id of the payer; blank = the sender."},
        "participants": {"type": "array", "items": {"type": "integer"},
                         # "tôi với Bình ăn" listed only Bình on one run, which
                         # charges a two-person bill to one person. The sender is
                         # a participant like anyone else — being the payer does
                         # not put them in, and saying "tôi" does not leave them out.
                         "description": "member ids of EVERYONE who ate, the sender included"
                                        " when they ate ('tôi với Bình ăn' = both ids)."},
        "total": {"type": "integer", "description": "Bill total, integer VND (840k → 840000)."},
        "guests": {"type": "array", "items": {"type": "string"},
                   "description": "Guest names (non-members who pay cash)."},
        "adjustments": {"type": "array", "items": {
            "type": "object",
            "properties": {"member": {"type": "integer"}, "amount": {"type": "integer"}},
            "required": ["member", "amount"]}},
        "items": {
            "type": "array",
            "description": (
                "Per-person mode ('ai ăn nấy trả'): the LIST price of what each person ate,"
                " copied straight off the bill — do NOT pre-apply the discount or split the"
                " difference yourself. One entry per participant, every participant exactly"
                " once. The tool prorates the gap between Σ items and `total` (promo, ship,"
                " service fee) across the items. Omit this to split the bill evenly."
                " **Only when you KNOW who ate what** — the user said so, or the bill writes a"
                " name next to each line. A bill that merely lists dishes does not say who"
                " ordered them: guessing changes what each person owes and looks correct while"
                " being invented. Split evenly instead."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "member": {"type": "integer", "description": "member id who ate this."},
                    "amount": {"type": "integer", "description": "Its price on the bill, integer VND."},
                    "label": {"type": "string", "description": "Dish name, e.g. 'cơm tấm'."},
                },
                "required": ["member", "amount"],
            },
        },
        "discount_split": {
            "type": "string",
            "enum": ["proportional", "equal"],
            "description": (
                "Only with `items`. How to share the gap between Σ items and `total`:"
                " 'proportional' (default) scales each dish price; 'equal' takes the same"
                " amount off everyone — use it when the user says so ('chia đều phần giảm',"
                " 'mỗi người trừ như nhau'). Either way the TOOL does the arithmetic."
            ),
        },
        "dish": {"type": "string", "description": "Dish (if the user mentioned it)."},
        "initiator": {"type": "string", "description": "Who initiated the meal (if any)."},
        "note": {"type": "string", "description": "Free-form note (e.g. 'An đổi ý')."},
        "day_word": {"type": "string", "description": "The day EXACTLY as the user said it ('thứ 5', 'hôm qua', '20/7'). The tool resolves it to a date (ICT) — never compute the date yourself. Omit = today."},
        "occurred_on": {"type": "string", "description": "Deprecated: pre-resolved meal date, ISO YYYY-MM-DD. Prefer `day_word` so the tool does the date math. Ignored when `day_word` is given."},
    },
    "required": ["participants", "total"],
}

_VOID_SCHEMA = {
    "type": "object",
    "properties": {"meal_id": {"type": "integer"}},
    "required": ["meal_id"],
}


def build(ctx, *, place_resolver=None) -> dict[str, PackTool]:
    """The lunch business's own tools: propose a meal, void one. The tools every
    ledger business shares live in ``packs.ledger_tools``.

    ``place_resolver(session, space_id, text) -> (place | None, confident)`` is the
    host's guess at which restaurant a dish text names, or ``None`` for a host
    without one. Place resolution is metadata and must never block the bill.
    """
    db = ctx.db

    def propose_meal(args, _tool_ctx=None) -> dict:
        args = args or {}
        try:
            participants = [int(p) for p in (args.get("participants") or [])]
        except (TypeError, ValueError):
            return _err("Invalid participant list.")
        total = args.get("total")
        if not isinstance(total, int):
            return _err("Missing total (integer VND).")
        if not participants:
            return _err("No participants provided.")
        guests = [str(g) for g in (args.get("guests") or [])]
        adjustments = {}
        for adj in args.get("adjustments") or []:
            try:
                adjustments[int(adj["member"])] = int(adj["amount"])
            except (KeyError, TypeError, ValueError):
                return _err("Each adjustment must have numeric {member, amount}.")
        payer = args.get("payer") or ctx.sender_member_id
        if not payer:
            return _err("Could not determine the payer.")
        dropped = _dropped_names(ctx, db, participants, payer, guests)
        if dropped:
            names = ", ".join(f"«{n}»" for n in dropped)
            if any(ctx.unknown_names.get(n) == "ambiguous" for n in dropped):
                return _err(
                    f"{names} khớp với hơn một người, và bữa này không có ai trong số họ. "
                    "HỎI người dùng là ai (kèm tên các ứng viên `find_members` trả về) "
                    "rồi mới đề xuất — đoán bừa là ghi nợ nhầm người."
                )
            return _err(
                f"{names} đã được tra trong lượt này nhưng không khớp thành viên nào, "
                "và cũng không có trong participants hay guests — chia như vậy là bỏ sót "
                "người ăn và mọi người phải trả nhiều hơn thực tế. Chọn MỘT cách rồi gọi lại: "
                "(1) người ngoài nhóm ăn cùng → thêm tên vào `guests`; "
                "(2) là thành viên nhưng viết khác → `find_members` lại bằng tên khác "
                "(tên thật, tên ngân hàng, biệt danh); "
                "(3) là người mới → `add_member` rồi cho id vào `participants`."
            )
        # Date resolution is authoritative here (like money-safety for amounts):
        # the model passes the user's day *word* and the tool computes the date,
        # so an LLM-computed occurred_on can never land a day off.
        day_word = args.get("day_word")
        occurred_on = args.get("occurred_on")
        if day_word:
            try:
                occurred_on = resolve_date(str(day_word), today=ctx.today()).isoformat()
            except ValueError as exc:
                return _err(str(exc))
        elif occurred_on is not None:
            try:
                _parse_iso(occurred_on)
            except ValueError:
                return _err("Ngày không hợp lệ (cần dạng YYYY-MM-DD).")

        items = args.get("items") or []
        discount_split = (args.get("discount_split") or "proportional").strip().lower()
        if items:
            if adjustments:
                return _err(
                    "Dùng `items` HOẶC `adjustments`, không dùng cả hai — "
                    "`items` đã là số tiền của từng người rồi."
                )
            if guests:
                return _err(
                    "Ghi theo món chưa hỗ trợ khách lẻ. Bỏ khách ra (chia đều), "
                    "hoặc ghi khách như một dòng món của người trả hộ."
                )
            try:
                items = normalize_items(items, participants)
                shares = prorate_items(
                    total, {i["member"]: i["amount"] for i in items},
                    discount_split=discount_split,
                )
                adjustments = itemized_adjustments(total, shares)
            except MoneyError as exc:
                return _err(str(exc))

        try:
            preview = split_with_guests(total, participants, len(guests), adjustments, payer_id=int(payer))
        except MoneyError as exc:
            return _err(str(exc))

        # Place resolution is metadata and must NEVER block the bill (design
        # D2): this is the deliberate opposite of the _dropped_names guard
        # above, because a missing eater bills everyone wrong while a missing
        # place tag only costs a statistic. Only confident tiers link; a weaker
        # guess rides the card instead, where one tap fixes it (D3).
        place_id = None
        place_guess = None
        dish_text = (args.get("dish") or "").strip()
        if dish_text and place_resolver is not None:
            with db.session() as s:
                place_guess, confident = place_resolver(s, ctx.space_id, dish_text)
            if place_guess is not None and confident:
                place_id = place_guess["id"]

        return {
            "ok": True,
            "type": "expense_draft",
            "payer_member_id": int(payer),
            "member_participants": participants,
            "guests": guests,
            "bill_total": total,
            "adjustments": [{"member": m, "amount": a} for m, a in adjustments.items()],
            "items": items,
            "discount_split": discount_split if items else None,
            "dish": args.get("dish"),
            "place_id": place_id,
            "place_guess": place_guess,
            "initiator": args.get("initiator"),
            "note": args.get("note"),
            "per_head_preview": preview["per_head"],
            "shares_preview": [{"member": m, "amount": a} for m, a in preview["shares"].items()],
            "occurred_on": occurred_on,
        }

    def void_meal(args, _tool_ctx=None) -> dict:
        args = args or {}
        meal_id = args.get("meal_id")
        if not isinstance(meal_id, int):
            return _err("Missing meal_id.")
        with db.session() as s:
            try:
                return {
                    "ok": True,
                    **ledger.void_meal(s, meal_id, room_id=ctx.space_id, by=str(ctx.sender_member_id)),
                }
            except ledger.LedgerError as exc:
                return _err(str(exc))

    specs = {
        "propose_meal": dict(
            execute=propose_meal,
            description="Propose a meal (does NOT record it) for the user to confirm. FINAL TOOL when logging a meal.",
            input_schema=_PROPOSE_SCHEMA,
        ),
        "void_meal": dict(
            execute=void_meal,
            description="Void a meal by meal_id to correct a mistake.",
            input_schema=_VOID_SCHEMA,
        ),
    }

    return {name: PackTool(name, spec["description"], spec["input_schema"], spec["execute"])
            for name, spec in specs.items()}
