from pathlib import Path

from app import skills


def test_materialize_writes_rules_and_skills(tmp_path):
    skills.materialize(str(tmp_path))
    rules = list((tmp_path / ".cursor" / "rules").glob("*.mdc"))
    assert rules, "expected at least one .mdc rule"
    text = rules[0].read_text("utf-8")
    assert text.startswith("---")
    assert "alwaysApply: true" in text
    assert (tmp_path / ".cursor" / "skills" / "record-payment" / "SKILL.md").exists()


def test_record_payment_skill_instructs_propose_payment_per_payer(tmp_path):
    skills.materialize(str(tmp_path))
    text = (tmp_path / ".cursor" / "skills" / "record-payment" / "SKILL.md").read_text("utf-8")
    # Uses the propose_payment tool, not propose_meal.
    assert "propose_payment" in text
    assert "propose_meal" in text  # explicitly told NOT to use it
    # One call per payer when several are named in one message.
    assert "MỘT LẦN CHO MỖI" in text
    # Omit amount for "đã trả / trả đủ" (server computes the pay-off).
    assert "BỎ TRỐNG `amount`" in text


def test_materialize_is_idempotent(tmp_path):
    skills.materialize(str(tmp_path))
    rule = next((tmp_path / ".cursor" / "rules").glob("*.mdc"))
    before = rule.stat().st_mtime_ns
    skills.materialize(str(tmp_path))  # unchanged → no rewrite
    assert rule.stat().st_mtime_ns == before
