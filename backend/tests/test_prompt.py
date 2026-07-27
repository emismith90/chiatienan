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
