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
    # `kind="text"` is what production actually stores for a human message; the
    # plan's guess of "user" matched nothing in 282 real rows.
    row = {"id": 101, "room_id": 1, "created_at": "2026-07-20T12:00:00+07:00",
           "kind": "text", "author_member_id": 7, "author": "Linh",
           "body": "@bot ghi bữa trưa", "attachments": {}}
    row.update(kw)
    return row


def _bot(**kw):
    row = {"id": 102, "room_id": 1, "created_at": "2026-07-20T12:00:05+07:00",
           "kind": "bot", "author_member_id": None, "author": "bot",
           "body": "Đã ghi.", "attachments": {}}
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
# expectation bootstrap — against the schema production actually uses
# --------------------------------------------------------------------------- #

def test_a_bot_reply_with_an_expense_draft_derives_propose_meal():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="@bot 300k cả nhóm"),
            _bot(id=2, kind="expense_draft", body="Bữa trưa 300.000đ",
                 attachments={"type": "expense_draft", "bill_total": 300000,
                              "payer_member_id": 7, "member_participants": [7, 8]})]
    cases = build_cases(rows, key_by_member_id={7: "A1", 8: "A2"})
    assert len(cases) == 1
    args = cases[0]["expect"]["args"]["propose_meal"]
    assert cases[0]["expect"]["tools"] == ["propose_meal"]
    assert args["total"] == 300000
    # prod member ids become corpus keys, since ids mean nothing in a bench room
    assert args["payer"] == "A1" and args["participants"] == ["A1", "A2"]
    assert not cases[0].get("review")


def test_cases_are_paired_backwards_from_bot_replies_not_from_at_bot():
    from bench.export_prod import build_cases
    # A bare reply to a bot question triggers a turn too
    # (chat.replies_to_bot_question), so forward-matching on "@bot" would drop it.
    rows = [_row(id=1, body="@bot ai trả tuần này"),
            _bot(id=2, attachments={"type": "settlement", "transfers": []}),
            _row(id=3, body="1"),
            _bot(id=4, kind="payment_draft",
                 attachments={"type": "payment_draft",
                              "transfers": [{"from_member_id": 7, "to_member_id": 8,
                                             "amount": 125000}]})]
    cases = build_cases(rows, key_by_member_id={7: "A1", 8: "A2"})
    assert [c["id"] for c in cases] == ["p1", "p3"]
    assert cases[1]["expect"]["args"]["propose_payment"] == {
        "from": "A1", "to": "A2", "amount": 125000}


def test_several_bot_rows_for_one_message_are_one_case():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="@bot 300k cả nhóm"),
            _bot(id=2, kind="expense_draft",
                 attachments={"type": "expense_draft", "bill_total": 300000,
                              "payer_member_id": 7, "member_participants": [7]}),
            _bot(id=3, body="Xong nhé.")]
    assert len(build_cases(rows, key_by_member_id={7: "A1"})) == 1


def test_a_multi_transfer_payment_card_drops_its_args_rather_than_grading_the_wrong_pair():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="@bot mọi người trả rồi"),
            _bot(id=2, kind="payment_draft", attachments={"type": "payment_draft",
                 "transfers": [{"from_member_id": 7, "to_member_id": 8, "amount": 1},
                               {"from_member_id": 9, "to_member_id": 8, "amount": 2}]})]
    case = build_cases(rows)[0]
    assert case["expect"]["tools"] == ["propose_payment"]
    assert "args" not in case["expect"]


def test_a_user_message_with_no_bot_reply_is_not_a_case_at_all():
    from bench.export_prod import build_cases
    # No bot row means no turn ran, so there is nothing to grade.
    assert build_cases([_row(id=1, body="ăn gì trưa nay")]) == []


def test_an_unrecognized_attachment_type_is_flagged_for_review():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="@bot ?"),
            _bot(id=2, body="hử", attachments={"type": "something_new"})]
    assert build_cases(rows)[0]["review"] is True


def test_a_commit_row_is_not_mistaken_for_a_turn():
    from bench.export_prod import build_cases
    # `meal` and `payment` attachments are Confirm presses, not LLM turns.
    rows = [_row(id=1, body="@bot 300k cả nhóm"),
            _bot(id=2, attachments={"type": "meal", "bill_total": 300000,
                                    "tracked_total": 300000})]
    assert build_cases(rows)[0]["review"] is True


def test_a_settlement_reply_derives_settle_period_with_no_member_args():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="@bot ai trả tuần này"),
            _bot(id=2, attachments={"type": "settlement", "transfers": []})]
    expect = build_cases(rows)[0]["expect"]
    assert expect["tools"] == ["settle_period"] and "args" not in expect


def test_the_bots_prose_is_kept_so_it_can_be_judged():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="@bot thêm A5"),
            _bot(id=2, body="Đã thêm A5 vào nhóm.", attachments={"type": "random_pick",
                 "ok": True, "chosen": "A5", "candidates": [], "label": "x"})]
    assert build_cases(rows)[0]["reply"] == "Đã thêm A5 vào nhóm."


def test_a_case_answered_from_an_earlier_bill_photo_is_image_tainted():
    from bench.export_prod import build_cases
    # chat.recent_images attaches a bill from an EARLIER message, so a text-only
    # row can still have been answered from a photo — and a total read off that
    # photo is unreproducible.
    rows = [_row(id=1, body="ảnh bill", attachments={"images": [{"data": "x"}]}),
            _row(id=2, body="@bot ghi đi"),
            _bot(id=3, kind="expense_draft",
                 attachments={"type": "expense_draft", "bill_total": 324200,
                              "payer_member_id": 7, "member_participants": [7]})]
    case = build_cases(rows, image_lookback=10)[0]
    assert case["had_images"] is True
    assert case["expect"]["tools"] == ["propose_meal"]
    assert "args" not in case["expect"]      # graded on tool selection only


def test_a_bill_photo_outside_the_lookback_window_does_not_taint():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="ảnh cũ", attachments={"images": [{"data": "x"}]}),
            _row(id=2, body="chuyện khác"),
            _row(id=3, body="@bot 300k cả nhóm"),
            _bot(id=4, kind="expense_draft",
                 attachments={"type": "expense_draft", "bill_total": 300000,
                              "payer_member_id": 7, "member_participants": [7]})]
    case = build_cases(rows, image_lookback=1, key_by_member_id={7: "A1"})[-1]
    assert case["had_images"] is False
    assert case["expect"]["args"]["propose_meal"]["total"] == 300000


# --------------------------------------------------------------------------- #
# non-member names, and the recorded baseline
# --------------------------------------------------------------------------- #

def test_guest_and_initiator_names_are_collected_from_the_log_itself():
    from bench.export_prod import collect_person_names
    # Reading the real sanitized output is what caught this: a meal's initiator is
    # a real person who is not a room member, so the member map never touches them.
    rows = [_bot(attachments={"type": "expense_draft", "guests": ["Emi"],
                              "initiator": "Trang Đinh"})]
    assert collect_person_names(rows) == ["Trang Đinh", "Emi"]   # longest first


def test_a_non_member_name_is_pseudonymized_out_of_a_body():
    from bench.export_prod import build_name_map, sanitize
    name_map, holders = build_name_map([{"id": 7, "display_name": "Linh"}],
                                       ["Trang Đinh"])
    out = sanitize([_row(body="thứ 3 ăn bún bò, Trang Đinh rủ, Linh trả 305k")],
                   name_map=name_map, holders=holders)
    assert "Trang Đinh" not in out[0]["body"]
    assert "G1" in out[0]["body"] and "A1" in out[0]["body"]


def test_a_redacted_account_holder_is_not_treated_as_a_name():
    from bench.export_prod import build_name_map
    # The export API already redacts it; adding "[redacted]" to the map would
    # replace that literal string everywhere and tell us nothing.
    _, holders = build_name_map([{"id": 7, "display_name": "Linh",
                                  "account_holder": "[redacted]"}])
    assert holders == []


def test_residual_name_candidates_surfaces_what_the_map_could_not():
    from bench.export_prod import residual_name_candidates
    found = residual_name_candidates([{"body": "A1 với Quyen đi ăn"}], {"A1"})
    assert "Quyen" in found and "A1" not in found


def test_the_recorded_baseline_records_what_prod_did():
    from bench.export_prod import build_baseline, build_cases
    rows = [_row(id=1, body="@bot 300k cả nhóm"),
            _bot(id=2, kind="expense_draft", body="Bữa trưa",
                 attachments={"type": "expense_draft", "bill_total": 300000,
                              "payer_member_id": 7, "member_participants": [7]})]
    cases = build_cases(rows, key_by_member_id={7: "A1"})
    baseline = build_baseline(cases, room=3)
    assert baseline["engine"] == "cursor" and baseline["repeat"] == 1
    # Stamped, because expectation and record come from the same log row: this
    # baseline passes by construction and measures nothing about Cursor.
    assert baseline["baseline_kind"] == "recorded-prod-log"
    rec = baseline["records"][0]
    assert rec["case_id"] == "p1" and rec["tools"][0]["name"] == "propose_meal"
    assert rec["tools"][0]["args"]["total"] == 300000


def test_review_flagged_cases_are_left_out_of_the_baseline():
    from bench.export_prod import build_baseline, build_cases
    rows = [_row(id=1, body="@bot ?"), _bot(id=2, attachments={"type": "mystery"})]
    assert build_baseline(build_cases(rows), room=3)["records"] == []


def test_an_ascii_folded_name_is_mapped_too():
    from bench.export_prod import build_name_map, sanitize
    # notes.build_qr_note ASCII-folds the debtor's name into the QR memo
    # (notes.py:78) and that memo is rendered into the bot's visible reply, so
    # "Hoàng" reaches the corpus as "Hoang". A diacritic-only map sails past it.
    name_map, holders = build_name_map([{"id": 7, "display_name": "Hoàng"}])
    out = sanitize([_row(body="ND: Hoang: T2 Grab Food")],
                   name_map=name_map, holders=holders)
    assert "Hoang" not in out[0]["body"] and "A1" in out[0]["body"]


def test_each_token_of_a_multi_word_name_is_mapped():
    from bench.export_prod import build_name_map, sanitize
    name_map, _ = build_name_map([{"id": 7, "display_name": "Emi Hoàng"}])
    out = sanitize([_row(body="Hoàng trả 100k")], name_map=name_map)
    assert "Hoàng" not in out[0]["body"]


def test_a_kinship_pronoun_inside_a_name_is_never_mapped_on_its_own():
    from bench.export_prod import build_name_map, sanitize
    # Substituting a pseudonym for "anh" would corrupt most messages in the room.
    name_map, _ = build_name_map([{"id": 7, "display_name": "anh Linh"}])
    out = sanitize([_row(body="anh ơi cho hỏi")], name_map=name_map)
    assert out[0]["body"] == "anh ơi cho hỏi"


def test_the_baseline_records_carry_grades_so_compare_has_a_denominator():
    from bench.export_prod import build_baseline, build_cases
    rows = [_row(id=1, body="@bot 300k cả nhóm"),
            _bot(id=2, kind="expense_draft", body="Bữa trưa",
                 attachments={"type": "expense_draft", "bill_total": 300000,
                              "payer_member_id": 7, "member_participants": [7]})]
    cases = build_cases(rows, key_by_member_id={7: "A1"})
    grades = build_baseline(cases, room=3)["records"][0]["grades"]
    assert grades["tool_selection"]["passed"] is True      # true by construction
    assert grades["ledger_state"]["passed"] is None        # no prod ledger
    assert grades["prose_quality"]["passed"] is None       # a card turn: no prose


def test_the_baseline_omits_message_bodies_because_it_is_committed():
    from bench.export_prod import build_baseline, build_cases
    rows = [_row(id=1, body="@bot 300k cả nhóm"),
            _bot(id=2, body="Đã ghi 300.000đ.", attachments={"type": "settlement",
                                                             "transfers": []})]
    baseline = build_baseline(build_cases(rows), room=3)
    rec = baseline["records"][0]
    # Design §11.4 commits the ids, expectations and SHA-256 — not the bodies.
    assert "message" not in rec and rec["final_text"] == ""
    assert baseline["bodies_included"] is False
    assert rec["grades"]["tool_selection"]["passed"] is True   # still gradeable
