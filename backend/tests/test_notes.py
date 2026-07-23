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


def test_build_note_missing_dish_shows_weekday_only():
    note = build_qr_note(
        "Hung",
        [{"date": date(2024, 1, 1), "dish": None},
         {"date": date(2024, 1, 2), "dish": "nem"}],
        fallback="x",
    )
    assert note == "Hung: T2, T3 nem"


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
