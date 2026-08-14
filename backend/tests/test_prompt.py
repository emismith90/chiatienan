from datetime import date
from app.prompt import build_system_prompt


def test_prompt_includes_today():
    p = build_system_prompt(sender_name="Giang", today=date(2026, 7, 22))
    assert "2026-07-22" in p
    assert "Giang" in p


def test_prompt_announces_attached_images():
    """Production 12:38 — the bill was attached and the model asked for the total
    that was in it. Images ride on the UserMessage, invisible to the prompt."""
    from app.agent import _render_prompt

    out = _render_prompt("@phoenix log đi", sender_name="Emi", image_count=1)
    assert "Ảnh kèm theo" in out
    assert "1 ảnh" in out
    assert "Đừng hỏi lại tổng tiền" in out


def test_prompt_is_unchanged_when_no_image_is_attached():
    from app.agent import _render_prompt

    assert "Ảnh kèm theo" not in _render_prompt("@phoenix số dư", sender_name="Emi")


def test_the_system_prompt_puts_the_room_tools_first_and_bash_last():
    """`bash` is available (PI_BUILTIN_TOOLS) but must be the last resort.

    Only a tool writes to the ledger, so a number the model works out with bash is
    wrong even when the arithmetic is right — it never reaches the books. The prompt
    has to say that outright, because availability alone reads as permission.
    """
    from app.prompt import build_system_prompt
    prompt = build_system_prompt()
    assert "Dùng công cụ của phòng trước tiên" in prompt
    assert "phương án cuối" in prompt
    assert "không dùng chúng để tính tiền" in prompt
    # and it must say what to do instead when no tool fits a money task
    assert "hỏi lại người dùng" in prompt


def test_the_money_safety_rules_rank_the_tools_explicitly():
    import re
    from pathlib import Path
    raw = Path("app/agent_skills/rules/money-safety.mdc").read_text(encoding="utf-8")
    # Collapse wrapping: where a phrase breaks across lines is cosmetic, and
    # asserting on the wrapped form would make reflowing the file fail the test.
    rules = re.sub(r"\s+", " ", raw)
    assert "Thứ tự ưu tiên CÔNG CỤ" in rules
    assert "PHƯƠNG ÁN CUỐI CÙNG" in rules
    # the priority block must come before the money rules it justifies
    assert rules.index("Thứ tự ưu tiên") < rules.index("Quy tắc TIỀN BẠC")
    assert "PHƯƠNG ÁN CUỐI CÙNG" in rules
    # naming the forbidden operations beats a general prohibition
    for operation in ("chia bill", "tính số dư", "mã QR"):
        assert operation in rules


def test_the_prompt_names_the_sender_and_their_member_id():
    """A name alone is not actionable — every tool takes member **ids**.

    Benchmark case `G4` ("tôi trả 250k, tôi với Bình ăn, Bình gọi thêm 50k") failed
    three times out of three by calling `find_members` twice and then asking *"bạn
    là ai trong nhóm nhỉ?"* — a question production never has to answer, because
    `chat.py` knows exactly who sent the message. Stating the id fixed the case; the
    same three prompts also stopped reaching for `bash`, because the arithmetic they
    were avoiding was never the problem.
    """
    prompt = build_system_prompt(sender_name="An", sender_id=7, today=date(2026, 7, 22))
    assert "«An»" in prompt
    assert "member_id=7" in prompt
    assert "ĐỪNG hỏi lại họ là ai" in prompt


def test_the_sender_line_survives_a_missing_id():
    # `sender_id` is optional: an unauthenticated or system turn still gets a name.
    prompt = build_system_prompt(sender_name="An", today=date(2026, 7, 22))
    # The sender line itself carries no id — `member_id` appears elsewhere in the
    # prompt (the `update_member` guidance), so the assertion is on the line.
    sender_line = next(l for l in prompt.splitlines() if "«An»" in l)
    assert "member_id" not in sender_line


def test_no_sender_means_no_sender_line_at_all():
    prompt = build_system_prompt(today=date(2026, 7, 22))
    assert "Người đang nhắn" not in prompt


def test_the_meal_skill_forbids_asking_who_the_sender_is():
    """The skill used to *license* the question: "Chỉ hỏi khi thiếu … ai trả"."""
    from pathlib import Path
    body = (Path(__file__).resolve().parent.parent / "app" / "agent_skills" / "skills"
            / "record-meal" / "SKILL.md").read_text(encoding="utf-8")
    assert 'TUYỆT ĐỐI không hỏi "bạn là ai"' in body
    # and the sender is an eater like anyone else — one run charged a two-person
    # bill to one person by leaving them out of `participants`
    assert "Người nhắn cũng là một người ăn" in body


def test_propose_meal_says_the_sender_counts_as_a_participant():
    from app.tools import tool_manifest
    schema = {t["name"]: t for t in tool_manifest()}["propose_meal"]["schema"]
    assert "sender included" in schema["properties"]["participants"]["description"]


def test_items_are_forbidden_when_nobody_said_who_ate_what():
    """`B2` charged Bình 80,308đ of a 261,000đ bill split three ways.

    The bill lists dishes; nobody said who ordered which. The model assigned them
    anyway — one dish per person, in bill order — and the numbers looked plausible.
    Inventing the allocation is worse than splitting evenly, because it is wrong in
    a way that reads as precision.
    """
    prompt = build_system_prompt(today=date(2026, 7, 22))
    assert "CHỈ dùng `items` khi biết chắc ai ăn món nào" in prompt
    assert "CHIA ĐỀU" in prompt

    from app.tools import tool_manifest
    items = {t["name"]: t for t in tool_manifest()}["propose_meal"]["schema"]["properties"]["items"]
    assert "Only when you KNOW who ate what" in items["description"]


def test_the_balances_skill_routes_settle_phrasings_to_settle_period():
    """"tính tiền" is a settle-up, not a statement.

    Four golden cases (`s5`, `s8`, `s10b`, `s12`) failed on this: the model answered
    with `member_statement` or `get_period_summary`, which lists both directions
    uncancelled — so "A nợ B 100k" and "B nợ A 100k" both appear and nobody learns
    what to transfer.
    """
    from pathlib import Path
    body = (Path(__file__).resolve().parent.parent / "app" / "agent_skills" / "skills"
            / "balances" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in ("'tính tiền'", "còn ai nợ ai gì không", "tôi phải trả bao nhiêu"):
        assert phrase in body, phrase
    assert "member_statement" in body and "settle_period" in body
