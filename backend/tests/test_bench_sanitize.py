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


def test_an_unrecognized_attachment_type_yields_a_prose_case():
    from bench.export_prod import build_cases
    # The room still read the reply, so it is gradeable as prose even though no
    # tool can be derived from an attachment shape this code does not know.
    rows = [_row(id=1, body="@bot ?"),
            _bot(id=2, body="hử, ý bạn là gì?", attachments={"type": "something_new"})]
    case = build_cases(rows)[0]
    assert not case.get("review") and case["expect"] == {}


def test_a_commit_row_is_not_a_turn_at_all():
    from bench.export_prod import build_cases
    # `meal` and `payment` attachments are a human pressing Confirm. Pairing one
    # would invent a case out of whatever text row preceded the button press.
    rows = [_row(id=1, body="chào cả nhà"),
            _bot(id=2, body="🍜 Đã ghi", attachments={"type": "meal",
                                                      "bill_total": 300000})]
    assert build_cases(rows) == []


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
    # Nothing said and no card: nothing to grade either way.
    rows = [_row(id=1, body="@bot ?"), _bot(id=2, body="", attachments={})]
    assert build_cases(rows)[0]["review"] is True
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


def test_a_prose_only_turn_is_a_case_not_a_leftover():
    from bench.export_prod import build_cases
    # Every attachment type that yields a tool is a CARD type, so a tool-gradable
    # turn is never prose-gradable. Discarding these would leave prose_quality with
    # no coverage anywhere in the harness.
    rows = [_row(id=1, body="@bot còn ai nợ ai không"),
            _bot(id=2, body="Không ai nợ ai cả nhé.", attachments={})]
    case = build_cases(rows)[0]
    assert not case.get("review")
    assert case["expect"] == {}          # tool_selection -> n/a
    assert case["reply"] == "Không ai nợ ai cả nhé."


def test_a_turn_with_no_card_and_no_words_is_still_flagged_for_review():
    from bench.export_prod import build_cases
    rows = [_row(id=1, body="@bot ?"), _bot(id=2, body="", attachments={})]
    assert build_cases(rows)[0]["review"] is True


def test_a_prose_case_is_graded_on_prose_and_not_on_tools():
    from bench.export_prod import build_baseline, build_cases
    rows = [_row(id=1, body="@bot còn ai nợ ai không"),
            _bot(id=2, body="Không ai nợ ai cả nhé.", attachments={})]
    grades = build_baseline(build_cases(rows), room=3,
                            judge=lambda *_: {"ok": True, "reason": "fine"}
                            )["records"][0]["grades"]
    assert grades["tool_selection"]["passed"] is None
    assert grades["prose_quality"]["passed"] is True


# --------------------------------------------------------------------------- #
# history — the replay's other half
# --------------------------------------------------------------------------- #

def test_a_case_carries_the_conversation_that_came_before_it():
    from bench.export_prod import build_cases
    # "@bot log" is a real prod message (p120) and it is unanswerable alone.
    # Production had the whole room in front of it; the corpus has to as well, or
    # the engine is graded on a question production never asked.
    rows = [_row(id=1, body="trưa nay 6 người ăn cơm gà"),
            _row(id=2, author_member_id=8, author="Kun", body="hết 324k anh Linh trả"),
            _row(id=3, body="@bot log"),
            _bot(id=4, body="", attachments={"type": "expense_draft",
                                             "payer_member_id": 7, "total": 324000,
                                             "shares": []})]
    case = build_cases(rows, key_by_member_id={7: "A1", 8: "A2"})[0]
    assert case["message"] == "@bot log"
    assert case["history"] == ("«A1»: trưa nay 6 người ăn cơm gà\n"
                              "«A2»: hết 324k anh Linh trả")


def test_history_renders_exactly_as_production_renders_it():
    from bench.export_prod import render_history
    # chat._render_messages: «Name» for a member, `phoenix` for the bot,
    # [ảnh: N] for a photo, oldest→newest. A replay fed a different history is
    # not a replay.
    rows = [_row(id=1, body="ai trả tiền hôm qua"),
            _bot(id=2, body="A1 trả 200k."),
            _row(id=3, body="", had_images=2),
            _row(id=4, body="@bot log")]
    rendered = render_history(rows, 3, lambda mid: "A1")
    assert rendered == ("«A1»: ai trả tiền hôm qua\n"
                        "phoenix: A1 trả 200k.\n"
                        "«A1»: [ảnh: 2]")


def test_the_history_window_is_bounded_and_keeps_the_most_recent():
    from bench.export_prod import render_history
    # ten earlier messages, then the trigger at index 10
    rows = [_row(id=i, body=f"m{i}") for i in range(1, 11)] + [_row(id=11, body="@bot log")]
    lines = render_history(rows, 10, lambda mid: "A1", window=3).splitlines()
    assert lines == ["«A1»: m8", "«A1»: m9", "«A1»: m10"]


def test_card_rows_stay_out_of_history_because_production_excludes_them():
    from bench.export_prod import render_history
    # chat.build_history filters kind.in_(("text", "bot")) — an expense_draft row
    # never entered the model's history, only the prose beside it.
    rows = [_row(id=1, body="ghi bữa trưa"),
            _bot(id=2, kind="expense_draft", body="",
                 attachments={"type": "expense_draft"}),
            _row(id=3, body="@bot log")]
    assert render_history(rows, 2, lambda mid: "A1") == "«A1»: ghi bữa trưa"


def test_a_long_body_is_clamped_in_history_like_production_clamps_it():
    from bench.export_prod import render_history
    rows = [_row(id=1, body="x" * 900), _row(id=2, body="@bot log")]
    line = render_history(rows, 1, lambda mid: "A1")
    assert line.endswith("…") and len(line) < 600


def test_a_card_after_a_commit_belongs_to_the_commit_not_to_the_message_above():
    from bench.export_prod import build_cases
    # Real sequence (prod rows 266-270): a joke, its prose answer, then a human
    # pressing Confirm on a payment — whose QR card carries `type: "settlement"`.
    # Walking back past the commit made the joke expect `settle_period`.
    rows = [_row(id=266, body="@bot cho tôi 1 số để đánh lô"),
            _bot(id=267, body="Số lô cho A5: **30**. Chúc trúng!", attachments={}),
            _bot(id=268, body="💸 A2 trả A5 155,000đ", attachments={"type": "payment"}),
            _bot(id=269, body="📱 QR chuyển khoản A3 → A5",
                 attachments={"type": "settlement", "transfers": []})]
    cases = {c["id"]: c for c in build_cases(rows)}
    assert cases["p266"]["expect"] == {}                  # a prose case, as it was
    assert cases["p266"]["reply"].startswith("Số lô")


# --------------------------------------------------------------------------- #
# prior_steps — the ledger the conversation is about
# --------------------------------------------------------------------------- #

def _ledger(meals=(), shares=(), payments=()):
    return {"meals": list(meals), "meal_shares": list(shares), "payments": list(payments)}


def _key(member_id):
    return {"6": "A3", "4": "A1", "9": "A6", "7": "A4",
            "5": "A2"}.get(str(member_id), member_id)


def test_a_prod_world_is_seeded_from_the_ledger_tables():
    """Without this a prod case replays against an empty ledger.

    `p20` ("@bot paid my part") answered *"bạn không nợ ai"* — correct for a room
    where nothing ever happened — and was graded as failing to call
    `propose_payment`.
    """
    from bench.export_prod import ledger_steps
    ledger = _ledger(
        meals=[{"id": 2, "occurred_on": "2026-07-22", "payer_member_id": 6,
                "total_amount": 305000, "voided": "False",
                "created_at": "2026-07-22 14:39:32"}],
        # five participants at 61,000 = the 305,000 total
        shares=[{"meal_id": 2, "member_id": m, "share_amount": 61000}
                for m in (4, 6, 9, 7, 5)],
        payments=[{"id": 1, "from_member_id": 4, "to_member_id": 6, "amount": 61000,
                   "occurred_on": "2026-07-22", "voided": "False",
                   "created_at": "2026-07-22 15:43:02"}])
    steps = ledger_steps(ledger, "2026-07-23 00:00:00", _key)
    kinds = [s["kind"] for s in steps]
    assert kinds == ["meal_confirmed", "payment"]      # and in recording order
    assert steps[0]["payer"] == "A3" and steps[0]["total"] == 305000
    assert steps[1] == {"id": "p1", "kind": "payment", "day": "2026-07-22",
                        "actor": "A1", "from": "A1", "to": "A3", "amount": 61000}


def test_only_the_ledger_as_of_the_turn_is_seeded():
    from bench.export_prod import ledger_steps
    ledger = _ledger(payments=[
        {"id": 1, "from_member_id": 4, "to_member_id": 6, "amount": 10000,
         "occurred_on": "2026-07-22", "voided": "False", "created_at": "2026-07-22 10:00:00"},
        {"id": 2, "from_member_id": 4, "to_member_id": 6, "amount": 20000,
         "occurred_on": "2026-07-22", "voided": "False", "created_at": "2026-07-22 18:00:00"}])
    steps = ledger_steps(ledger, "2026-07-22 12:00:00", _key)
    assert [s["amount"] for s in steps] == [10000]


def test_a_voided_meal_is_not_seeded():
    """Nothing in the conversation records a `void_meal`, which is why the cards
    cannot be the source: seeding from them kept a voided meal and put 868,000đ of
    phantom credit on one member."""
    from bench.export_prod import ledger_steps
    ledger = _ledger(
        meals=[{"id": 3, "occurred_on": "2026-07-22", "payer_member_id": 6,
                "total_amount": 100000, "voided": "True", "created_at": "2026-07-22 10:00:00"}],
        shares=[{"meal_id": 3, "member_id": 6, "share_amount": 50000},
                {"meal_id": 3, "member_id": 4, "share_amount": 50000}])
    assert ledger_steps(ledger, "2026-07-30 00:00:00", _key) == []


def test_unequal_shares_survive_as_adjustments():
    """The Grab Food meal: 324,200đ over six, but 54,500 / 79,200 / 27,000 each.

    A committed `meal` attachment keeps `shares` and drops the `adjustments` behind
    them, so re-deriving from the bill split it evenly (54,033 each) and every
    later balance in the corpus was wrong by a few thousand đồng.
    """
    from bench.export_prod import ledger_steps
    shares = {6: 79200, 4: 54500, 9: 27000}
    ledger = _ledger(
        meals=[{"id": 6, "occurred_on": "2026-07-27", "payer_member_id": 4,
                "total_amount": sum(shares.values()), "voided": "False",
                "created_at": "2026-07-27 12:00:00"}],
        shares=[{"meal_id": 6, "member_id": m, "share_amount": a} for m, a in shares.items()])
    step = ledger_steps(ledger, "2026-07-28 00:00:00", _key)[0]
    from app.money import split_shares
    ids = {"A3": 6, "A1": 4, "A6": 9}
    got = split_shares(step["total"], [ids[p] for p in step["participants"]],
                       {ids[a["member"]]: a["amount"] for a in step["adjustments"]})
    assert got == shares          # the split reproduces production exactly


def test_a_member_the_corpus_cannot_name_is_skipped_not_crashed():
    from bench.export_prod import ledger_steps
    ledger = _ledger(payments=[{"id": 1, "from_member_id": 999, "to_member_id": 6,
                                "amount": 10000, "occurred_on": "2026-07-22",
                                "voided": "False", "created_at": "2026-07-22 10:00:00"}])
    assert ledger_steps(ledger, "2026-07-30 00:00:00", _key) == []


def test_a_member_who_had_not_joined_yet_is_not_in_the_room():
    """`p12` says "chia 5 trừ A2" in a room that had six members that day.

    A7 joined nine days later. Seeding all seven makes "five of them, not A2"
    genuinely ambiguous, and the model stopped to ask which five — a question
    production never had to ask.
    """
    from bench.export_prod import members_at
    members = [{"id": 4, "created_at": "2026-07-22 10:31:23"},
               {"id": 6, "created_at": "2026-07-22 10:33:37"},
               {"id": 11, "created_at": "2026-07-31 10:07:44"}]
    key_by_id = {"4": "A1", "6": "A3", "11": "A7"}
    early = members_at(members, key_by_id, "2026-07-22 14:39:00")
    assert [m["key"] for m in early] == ["A1", "A3"]
    later = members_at(members, key_by_id, "2026-08-04 12:00:00")
    assert [m["key"] for m in later] == ["A1", "A3", "A7"]
    assert early[0] == {"key": "A1", "display_name": "A1", "nickname": "a1"}


def test_the_roster_skips_members_the_name_map_does_not_cover():
    from bench.export_prod import members_at
    assert members_at([{"id": 99, "created_at": "2026-07-01"}], {"4": "A1"},
                      "2026-08-01") == []


def test_a_decomposed_name_is_still_replaced():
    """One real message body arrived NFD: "Nhím" as `N h i ◌́ m`.

    The member table holds the composed form, so the pattern matched nothing and a
    member's nickname went into the corpus unredacted. Both sides normalize now.
    """
    import unicodedata
    decomposed = unicodedata.normalize("NFD", "tôi đã trả tiền cho Nhím")
    assert decomposed != "tôi đã trả tiền cho Nhím"          # genuinely NFD
    out = sanitize([_row(body=decomposed)], name_map={"Nhím": "A5"})
    assert "A5" in out[0]["body"]
    assert "Nh" not in out[0]["body"]


def test_a_decomposed_member_name_still_builds_its_forms():
    import unicodedata
    from bench.export_prod import build_name_map
    name_map, _ = build_name_map([
        {"id": 8, "display_name": unicodedata.normalize("NFD", "Nhím")}])
    assert name_map.get("Nhím") == "A1"          # composed lookup works
    assert name_map.get("Nhim") == "A1"          # and the folded form


def test_extra_aliases_are_read_from_the_local_file():
    """Human review found two given names the member table cannot supply. The
    mechanism is committed; the names live in a gitignored file."""
    import textwrap
    from pathlib import Path
    import tempfile
    from bench.export_prod import build_name_map, load_extra_aliases
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "extra_aliases.txt"
        path.write_text(textwrap.dedent("""\
            # a comment
            A1 = Someone
            A2 = Another Person
            not-a-line
        """), encoding="utf-8")
        aliases = load_extra_aliases(path)
    assert aliases == {"Someone": "A1", "Another Person": "A2"}
    name_map, _ = build_name_map([{"id": 4, "display_name": "Emi"}], [], aliases)
    assert name_map["Someone"] == "A1"
    assert name_map["Emi"] == "A1"                # a real member form still wins


def test_a_missing_alias_file_is_the_normal_state():
    from pathlib import Path
    from bench.export_prod import load_extra_aliases
    assert load_extra_aliases(Path("/nonexistent/extra_aliases.txt")) == {}


def test_a_lowercase_name_after_a_kinship_pronoun_is_surfaced_for_review():
    """Title-case only missed a given name written in lowercase, and truncating the
    list to the top 20 hid another. "anh <word>" is the strongest signal there is."""
    from bench.export_prod import residual_name_candidates
    rows = [{"body": "@bot đã trả anh hưng rồi"}]
    assert "hưng" in residual_name_candidates(rows, allowed=set())


def test_a_body_hiding_in_a_tool_result_is_dropped_with_the_others():
    """`bodies_included: false` dropped `message` and kept `result.raw_input` — the
    user's own sentence, stored so a draft card can show what it was built from. One
    real message rode into a committed file that way."""
    from bench.export_prod import build_baseline
    rows = [_row(id=1, body="@bot I paid 335k, Someone, và khách"),
            _bot(id=2, kind="expense_draft",
                 attachments={"type": "expense_draft", "bill_total": 335000,
                              "payer_member_id": 7, "member_participants": [7, 8],
                              "raw_input": "@bot I paid 335k, Someone, và khách"})]
    from bench.export_prod import build_cases
    baseline = build_baseline(build_cases(rows, key_by_member_id={7: "A1", 8: "A2"}),
                              room=3, keep_bodies=False)
    blob = json.dumps(baseline, ensure_ascii=False)
    assert "raw_input" not in blob
    assert "335k" not in blob            # the sentence is gone, the amount arg stays
    assert baseline["records"][0]["tools"][0]["args"]["total"] == 335000


def test_verify_matches_a_name_on_word_boundaries_case_and_form():
    """Three near-misses, each of which let a name through once: a substring match
    flags "nhưng" for containing a name, a case-sensitive one misses a lowercase
    given name, and an unnormalized one misses a decomposed vowel."""
    import unicodedata
    from pathlib import Path
    import tempfile
    from bench.export_prod import verify
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "out.json"
        path.write_text('{"a": "nhưng giá cao hơn", "b": "'
                        + unicodedata.normalize("NFD", "trả anh hưng rồi") + '"}',
                        encoding="utf-8")
        found = verify(path, [], ["Hưng"])
    assert found == ["name 'Hưng'"]      # the decomposed, lowercase one — not "nhưng"
