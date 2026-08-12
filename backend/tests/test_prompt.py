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

    out = _render_prompt("@bot log đi", sender_name="Emi", image_count=1)
    assert "Ảnh kèm theo" in out
    assert "1 ảnh" in out
    assert "Đừng hỏi lại tổng tiền" in out


def test_prompt_is_unchanged_when_no_image_is_attached():
    from app.agent import _render_prompt

    assert "Ảnh kèm theo" not in _render_prompt("@bot số dư", sender_name="Emi")


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
