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


def test_materialize_prunes_files_this_build_no_longer_ships(tmp_path):
    """The workspace lives on the mounted volume now, so it outlives the
    container: a renamed or deleted skill would otherwise keep being loaded."""
    skills.materialize(str(tmp_path))
    orphan_skill = tmp_path / ".cursor" / "skills" / "retired-skill" / "SKILL.md"
    orphan_skill.parent.mkdir(parents=True, exist_ok=True)
    orphan_skill.write_text("stale guidance", "utf-8")
    orphan_rule = tmp_path / ".cursor" / "rules" / "old-rule.mdc"
    orphan_rule.write_text("stale rule", "utf-8")

    skills.materialize(str(tmp_path))

    assert not orphan_skill.exists()
    assert not orphan_skill.parent.exists(), "an emptied skill dir should go too"
    assert not orphan_rule.exists()
    assert (tmp_path / ".cursor" / "skills" / "record-meal" / "SKILL.md").exists()
    assert list((tmp_path / ".cursor" / "rules").glob("*.mdc"))


def test_money_rule_covers_uneven_splits_and_stale_drafts(tmp_path):
    """Both were in an on-demand skill the model skipped; the rule is always applied."""
    skills.materialize(str(tmp_path))
    rule = (tmp_path / ".cursor" / "rules" / "money-safety.mdc").read_text("utf-8")
    assert "items" in rule and "cancel_draft" in rule
