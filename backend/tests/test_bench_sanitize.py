"""The prod-corpus sanitizer.

Every test here is a redaction requirement, not a nice-to-have. The corpus file
is gitignored, so a leak cannot happen *via git* — but it still lands on a
developer's disk and gets read by a model, so the bodies have to be clean.

The non-obvious half: bank details reach this system **through chat**.
`add_member` and `update_member` take `bank_code` / `account_number` /
`account_holder` as tool arguments (`tools.py:178-211`), so a real message body
reads `@bot cập nhật stk của tôi 0071000123456 VCB NGUYEN VAN A` — and `body` is
exactly what the corpus keeps. A key denylist over `attachments` does not touch
that.
"""
import json

from bench.export_prod import sanitize


def _row(**kw):
    row = {"id": 101, "room_id": 1, "created_at": "2026-07-20T12:00:00+07:00",
           "kind": "user", "author_member_id": 7, "author": "Linh",
           "body": "@bot ghi bữa trưa", "attachments": {}}
    row.update(kw)
    return row


def test_bank_details_are_stripped_from_attachments():
    out = sanitize([_row(attachments={"transfers": [
        {"to": "Linh", "amount": 100000, "account_number": "0123456789",
         "account_holder": "NGUYEN VAN A", "bank_code": "VCB",
         "qr_url": "https://img.vietqr.io/image/VCB-0123456789-compact2.png?amount=100000"}]})])
    blob = json.dumps(out)
    for secret in ("0123456789", "NGUYEN VAN A", "VCB", "img.vietqr.io"):
        assert secret not in blob


def test_account_numbers_typed_into_chat_are_redacted():
    # tools.py:178-211 — bank details enter via add_member/update_member args
    out = sanitize([_row(body="@bot cập nhật stk của tôi 0071000123456 VCB NGUYEN VAN A")],
                   name_map={}, holders=["NGUYEN VAN A"])
    blob = json.dumps(out)
    assert "0071000123456" not in blob
    assert "NGUYEN VAN A" not in blob


def test_digit_runs_of_eight_or_more_go_but_amounts_stay():
    out = sanitize([_row(body="@bot 324200 grab, stk 19001234567")])
    assert "324200" in out[0]["body"]        # a VND amount — graded, must survive
    assert "19001234567" not in out[0]["body"]


def test_a_seven_digit_amount_survives():
    # VND amounts in this corpus are <=7 digits or carry a k/tr/đ unit.
    out = sanitize([_row(body="@bot bill 1500000 chia đều")])
    assert "1500000" in out[0]["body"]


def test_names_are_matched_on_word_boundaries_not_substrings():
    # bare longest-first replacement mangles Vietnamese: "An" sits inside "anh"
    out = sanitize([_row(author="An", body="anh An trả 300k, cảm ơn anh")],
                   name_map={"An": "A1"})
    assert out[0]["body"] == "anh A1 trả 300k, cảm ơn anh"


def test_a_diacritic_name_is_replaced_whole():
    out = sanitize([_row(body="Cường với Cường Anh cùng ăn")],
                   name_map={"Cường": "A2", "Cường Anh": "A3"})
    # longest first, so "Cường Anh" is not shredded into "A2 Anh"
    assert out[0]["body"] == "A2 với A3 cùng ăn"


def test_the_map_covers_nicknames_aliases_and_holders_not_just_display_names():
    out = sanitize([_row(body="@bot linhle trả 100k")],
                   name_map={"Linh": "A1", "linhle": "A1"})
    assert "linhle" not in json.dumps(out)


def test_the_author_column_is_pseudonymized_too():
    out = sanitize([_row(author="Linh")], name_map={"Linh": "A1"})
    assert out[0]["author"] == "A1"
    assert "Linh" not in json.dumps(out)


def test_base64_images_become_the_anh_marker():
    out = sanitize([_row(attachments={"images": [{"data": "iVBORw0KGgo…"}]})])
    assert "iVBORw" not in json.dumps(out)
    assert out[0]["had_images"] == 1


def test_invite_tokens_and_pins_are_stripped():
    out = sanitize([_row(attachments={"invite_token": "abc123", "pin": "4321"})])
    blob = json.dumps(out)
    assert "abc123" not in blob and "4321" not in blob


def test_denylisted_keys_are_stripped_at_any_depth():
    out = sanitize([_row(attachments={"a": {"b": [{"c": {"account_number": "999888777"}}]}})])
    assert "999888777" not in json.dumps(out)


def test_a_name_appearing_inside_an_attachment_string_is_replaced():
    out = sanitize([_row(attachments={"transfers": [{"from_name": "Linh", "amount": 1000}]})],
                   name_map={"Linh": "A1"})
    blob = json.dumps(out)
    assert "Linh" not in blob and "A1" in blob


def test_the_sanitizer_is_pure_and_does_not_mutate_its_input():
    row = _row(body="@bot Linh trả 300k", attachments={"account_number": "123456789"})
    sanitize([row], name_map={"Linh": "A1"})
    assert row["body"] == "@bot Linh trả 300k"
    assert row["attachments"] == {"account_number": "123456789"}


def test_name_map_from_members_covers_every_alias_form():
    from bench.export_prod import build_name_map
    members = [{"id": 7, "display_name": "Linh", "nickname": "linhle",
                "aliases": ["Linh Lê", "chị Linh"], "account_holder": "LE THI LINH"}]
    name_map, holders = build_name_map(members)
    for form in ("Linh", "linhle", "Linh Lê", "chị Linh"):
        assert name_map[form] == "A1", form
    assert "LE THI LINH" in holders


def test_two_members_get_distinct_pseudonyms():
    from bench.export_prod import build_name_map
    name_map, _ = build_name_map([{"id": 7, "display_name": "Linh"},
                                  {"id": 9, "display_name": "Giang"}])
    assert {name_map["Linh"], name_map["Giang"]} == {"A1", "A2"}


# --------------------------------------------------------------------------- #
# expectation bootstrap
# --------------------------------------------------------------------------- #

def test_a_bot_reply_with_an_expense_draft_derives_propose_meal():
    from bench.export_prod import build_cases
    rows = [_row(id=1, kind="user", body="@bot 300k cả nhóm"),
            _row(id=2, kind="bot", author="bot", body="Bữa trưa 300.000đ",
                 attachments={"type": "expense_draft", "bill_total": 300000})]
    cases = build_cases(rows)
    assert len(cases) == 1
    assert cases[0]["expect"]["tools"] == ["propose_meal"]
    assert cases[0]["expect"]["args"]["propose_meal"]["total"] == 300000
    assert not cases[0].get("review")


def test_a_user_message_with_no_bot_reply_is_flagged_for_review():
    from bench.export_prod import build_cases
    cases = build_cases([_row(id=1, kind="user", body="@bot ơi")])
    assert cases[0]["review"] is True


def test_an_unrecognized_attachment_type_is_flagged_for_review():
    from bench.export_prod import build_cases
    rows = [_row(id=1, kind="user", body="@bot ?"),
            _row(id=2, kind="bot", author="bot", body="hử",
                 attachments={"type": "something_new"})]
    assert build_cases(rows)[0]["review"] is True


def test_a_settlement_reply_derives_settle_period_with_no_member_args():
    from bench.export_prod import build_cases
    rows = [_row(id=1, kind="user", body="@bot ai trả tuần này"),
            _row(id=2, kind="bot", author="bot", body="Tạm tính…",
                 attachments={"type": "settlement", "transfers": []})]
    expect = build_cases(rows)[0]["expect"]
    # Prod has no reconstructable ledger, and prod member ids mean nothing in a
    # bench room, so only the tool name is gradable.
    assert expect["tools"] == ["settle_period"] and "args" not in expect


def test_a_case_answered_from_an_earlier_bill_photo_is_image_tainted():
    from bench.export_prod import build_cases
    # chat.recent_images attaches a bill from an EARLIER message, so a text-only
    # row can still have been answered from a photo — and a total read off that
    # photo is unreproducible.
    rows = [_row(id=1, kind="user", body="ảnh bill", attachments={"images": [{"data": "x"}]}),
            _row(id=2, kind="user", body="@bot ghi đi"),
            _row(id=3, kind="bot", author="bot", body="ok",
                 attachments={"type": "expense_draft", "bill_total": 324200})]
    case = build_cases(rows, image_lookback=10)[0]   # only row 2 mentions @bot
    assert case["had_images"] is True
    assert case["expect"]["tools"] == ["propose_meal"]
    assert "args" not in case["expect"]      # graded on tool selection only


def test_a_bill_photo_outside_the_lookback_window_does_not_taint():
    from bench.export_prod import build_cases
    rows = [_row(id=1, kind="user", body="ảnh cũ", attachments={"images": [{"data": "x"}]}),
            _row(id=2, kind="user", body="chuyện khác"),
            _row(id=3, kind="user", body="@bot 300k cả nhóm"),
            _row(id=4, kind="bot", author="bot", body="ok",
                 attachments={"type": "expense_draft", "bill_total": 300000})]
    case = build_cases(rows, image_lookback=1)[-1]
    assert case["had_images"] is False
    assert case["expect"]["args"]["propose_meal"]["total"] == 300000


def test_only_bot_mentioning_user_rows_become_cases():
    from bench.export_prod import build_cases
    rows = [_row(id=1, kind="user", body="ăn gì trưa nay"),
            _row(id=2, kind="user", body="@bot 300k cả nhóm"),
            _row(id=3, kind="bot", author="bot", body="ok",
                 attachments={"type": "expense_draft", "bill_total": 300000})]
    assert [c["message"] for c in build_cases(rows)] == ["@bot 300k cả nhóm"]
