from datetime import date

from app.notes import _ascii_fold, _weekday_label, build_qr_note


def test_weekday_label_vietnamese_mon_to_sun():
    # 2024-01-01 was a Monday; walk the week.
    assert _weekday_label(date(2024, 1, 1)) == "T2"
    assert _weekday_label(date(2024, 1, 2)) == "T3"
    assert _weekday_label(date(2024, 1, 3)) == "T4"
    assert _weekday_label(date(2024, 1, 4)) == "T5"
    assert _weekday_label(date(2024, 1, 5)) == "T6"
    assert _weekday_label(date(2024, 1, 6)) == "T7"
    assert _weekday_label(date(2024, 1, 7)) == "CN"


def test_ascii_fold_strips_vietnamese_diacritics_and_dstroke():
    assert _ascii_fold("bún chả") == "bun cha"
    assert _ascii_fold("phở") == "pho"
    assert _ascii_fold("Đặng") == "Dang"
    assert _ascii_fold("") == ""


def test_build_note_basic_name_and_meals():
    note = build_qr_note(
        "Hung",
        [{"date": date(2024, 1, 1), "dish": "bún chả"},
         {"date": date(2024, 1, 2), "dish": "nem"}],
        fallback="Chia tien an",
    )
    assert note == "Hung: T2 bun cha, T3 nem"


def test_build_note_sorts_by_date():
    note = build_qr_note(
        "Hung",
        [{"date": date(2024, 1, 2), "dish": "nem"},
         {"date": date(2024, 1, 1), "dish": "pho"}],
        fallback="x",
    )
    assert note == "Hung: T2 pho, T3 nem"


def test_build_note_missing_dish_still_names_the_meal():
    """Production: meal #5 was logged with no dish, so Giang's memo read
    "Giang Hoang: T5 bun cha rua xe, T6" and he reported it as wrong twice."""
    note = build_qr_note(
        "Hung",
        [{"date": date(2024, 1, 1), "dish": None},
         {"date": date(2024, 1, 2), "dish": "nem"}],
        fallback="x",
    )
    assert note == "Hung: T2 bua trua, T3 nem"


def test_build_note_empty_meals_returns_fallback():
    assert build_qr_note("Hung", [], fallback="Chia tien an 22/7") == "Chia tien an 22/7"


def test_build_note_truncates_with_overflow_marker_under_budget():
    meals = [
        {"date": date(2024, 1, 1), "dish": "com tam"},
        {"date": date(2024, 1, 2), "dish": "bun bo"},
        {"date": date(2024, 1, 3), "dish": "banh mi"},
        {"date": date(2024, 1, 4), "dish": "hu tieu"},
    ]
    note = build_qr_note("Hung", meals, fallback="x", budget=30)
    assert len(note) <= 30
    assert note.startswith("Hung: T2 com tam")
    # remaining meals collapse into a +N marker
    assert note.endswith("+2") or note.endswith("+3")


def test_build_note_all_fit_no_marker():
    meals = [{"date": date(2024, 1, 1), "dish": "pho"}]
    note = build_qr_note("Hung", meals, fallback="x", budget=50)
    assert note == "Hung: T2 pho"
    assert "+" not in note


def test_settlement_note_lists_only_meals_still_owed(db):
    """Production msg#92: Giang's 107,000đ QR was noted "T4 bun bo hue, T5 bun
    cha rua xe, T6" — but he had already paid the T4 bún bò huế four days
    earlier. He reported the memo as wrong twice, and he was right."""
    from datetime import date

    from app import ledger
    from app.tools import ToolContext, build_tools
    from tests.test_ledger import _seed_room

    room_id, (giang, linh, other) = _seed_room(db, 3)
    with db.session() as s:
        paid_off = ledger.record_meal(
            s, room_id=room_id, payer_member_id=linh, participants=[giang, linh],
            total_amount=122_000, adjustments={}, guests=[], dish="bún bò huế",
            occurred_on=date(2026, 7, 22), logged_by=str(linh),
        )
        ledger.record_meal(
            s, room_id=room_id, payer_member_id=linh, participants=[giang, linh],
            total_amount=80_000, adjustments={}, guests=[], dish="bún chả rửa xe",
            occurred_on=date(2026, 7, 23), logged_by=str(linh),
        )
        # Giang settles the first meal specifically, exactly as he did in prod.
        ledger.record_payment(
            s, room_id=room_id, from_member_id=giang, to_member_id=linh,
            amount=paid_off["shares"][giang], note="bún bò huế", logged_by=str(giang),
        )

    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=giang,
                                   sender_name="Giang", turn_mentions=[]))
    out = tools["settle_period"].execute({"keyword": "since_last"})

    assert out["ok"], out
    rows = [t for t in out["transfers"] if t["from_id"] == giang and t["to_id"] == linh]
    assert len(rows) == 1
    note = rows[0]["note"]
    assert "bun cha rua xe" in note
    assert "bun bo hue" not in note, f"already-paid meal is still in the memo: {note!r}"
