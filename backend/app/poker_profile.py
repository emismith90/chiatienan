"""The poker business's seeded profile (plan Task 6.3, decision 4): the pack's content
(prompt, skills, rules) on this host's models, caps, runtime and pipeline. The bot
identity stays the host's (one handle per host — Phase 6 review F6); the shared
money-safety core comes from ``ledger_tools``, the poker addendum from the pack."""
from __future__ import annotations

from app.config import Settings, settings as _settings
from app.default_profile import build_default_spec
from kernos.content import ProfileSpec, Prompt, Rule, Skill, ToolPackRef, ValidationRuleRef
from packs.ledger_tools import LedgerToolsPack
from packs.poker_ledger import PokerLedgerPack

BUSINESS_SLUG = "poker"

#: What the table sees instead of a forged "recorded" claim — names no meal (review F6).
FORGERY_BODY = ("⚠️ Nothing was **recorded** — the ledger did not change.\n"
                "Say it again with every player's buy-in and cash-out; a game reaches the ledger only "
                "once a draft card appears and someone presses **Confirm**.")

VALIDATION = [
    ValidationRuleRef(id="chips-conserved", scope="tool_args", plugin="kernos.validate.sum_equals", tool="propose_game",
                      config={"left": "entries[*].buy_in", "right": ["entries[*].cash_out", "house"]}, on_fail="return_error"),
    ValidationRuleRef(id="no-negative-chips", scope="tool_args", plugin="kernos.validate.non_negative", tool="propose_game",
                      config={"paths": ["entries[*].buy_in", "entries[*].cash_out", "house"]}, on_fail="return_error"),
    ValidationRuleRef(id="one-entry-per-player", scope="tool_args", plugin="kernos.validate.unique_members", tool="propose_game",
                      config={"path": "entries"}, on_fail="return_error"),
]


def poker_content() -> dict:
    core = LedgerToolsPack.content()
    poker = PokerLedgerPack().content()
    return {"prompt_body": poker["prompt_body"], "skills": poker["skills"], "rules": core["rules"] + poker["rules"]}


def poker_sources() -> list[dict]:
    c = poker_content()
    out = [{"kind": "skill", "slug": k["name"], "title": k["name"], "body": k["body"],
            "frontmatter": {"description": k["description"], "delivery": "inline"}} for k in c["skills"]]
    out += [{"kind": "rule", "slug": r["slug"], "title": r["slug"], "body": r["content"],
             "frontmatter": {"tags": r["tags"]}} for r in c["rules"]]
    return out


def build_poker_spec(settings: Settings | None = None) -> ProfileSpec:
    base = build_default_spec(settings or _settings)
    c = poker_content()
    pipeline = {stage: list(entries) for stage, entries in base.pipeline.items()}
    pipeline["validate"] = [
        e.model_copy(update={"config": {"body": FORGERY_BODY}}) if e.id == "app.validate.fabricated_commit" else e
        for e in pipeline["validate"]]
    return base.model_copy(update={
        "pipeline": pipeline,
        "prompt": Prompt(body=c["prompt_body"], append=[]),
        "rules": [Rule(slug=r["slug"], content=r["content"], tags=list(r["tags"])) for r in c["rules"]],
        "skills": [Skill(name=k["name"], description=k["description"], body=k["body"]) for k in c["skills"]],
        "tool_packs": [ToolPackRef(pack="poker_ledger"), ToolPackRef(pack="ledger_tools"), ToolPackRef(pack="room_members")],
        "validation": list(VALIDATION),
        "meta": {"handles_money": True, "business": BUSINESS_SLUG},
    })
