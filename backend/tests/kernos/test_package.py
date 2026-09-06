"""A profile as a Pi package and back (design §12; plan Task 9.2, review F4–F7)."""
import json

import pytest

from app.kernel import kernel_for
from kernos.content import Conflict, Invalid, ProfileSpec, export_profile, import_package
from kernos.content.package import parse_frontmatter, pi_skill_name, secret_paths, sources_from_pi_files
from kernos.eval import spec_sha


def test_export_is_a_pi_package_with_a_lossless_kernos_json(db):
    k = kernel_for(db)
    pid = k.seed_report["profile_id"]
    files = export_profile(k.store, pid)
    text = {p: b.decode() for p, b in files.items()}
    pkg = json.loads(text["package.json"])
    assert pkg["keywords"] == ["pi-package"] and pkg["pi"] == {"skills": ["./skills"], "prompts": ["./prompts"]}
    assert pkg["kernos"]["business"] == "lunch" and pkg["kernos"]["version"] == 1
    published = k.store.published_spec(pid)
    assert pkg["kernos"]["spec_sha"] == spec_sha(ProfileSpec.model_validate(published))
    skill = text["skills/record-meal/SKILL.md"]
    meta, body = parse_frontmatter(skill)
    assert meta["name"] == "record-meal" and meta["description"] and body.strip()
    assert all(pi_skill_name(p.split("/")[1]) == p.split("/")[1] for p in text if p.startswith("skills/"))
    agents = text["AGENTS.md"]
    assert agents.startswith(published["prompt"]["body"][:40]) and "## Rule: money-safety" in agents
    settings = json.loads(text[".pi/settings.json"])
    assert settings["defaultModel"] and set(settings) <= {"defaultProvider", "defaultModel", "defaultThinkingLevel",
                                                          "thinkingBudgets", "compaction", "retry", "steeringMode"}
    kernos = json.loads(text["kernos.json"])
    assert "runtime" not in kernos and kernos == published
    assert next(r for r in kernos["rules"] if r["slug"] == "money-safety")["tags"] == ["money"]
    blob = "\n".join(text.values()).lower()
    assert "open_router_key" not in blob and "test-openrouter-key" not in blob
    assert "pi -e ." in text["README.md"]
    # a specific version, and a profile with nothing published
    assert export_profile(k.store, pid, version_id=k.seed_report["version_id"]) == files
    empty = k.store.create_profile(k.seed_report["business_id"], "empty")
    with pytest.raises(Invalid, match="no published version"):
        export_profile(k.store, empty["id"])


def test_export_refuses_credentials_and_drops_path_settings(db):
    k = kernel_for(db)
    pid = k.seed_report["profile_id"]
    d = k.store.create_draft(pid, actor="admin")
    k.store.update_draft(d["id"], {"extensions": [{"id": "x", "config": {"apiKey": "sk-abcdefghijkl"}}]}, actor="admin")
    with pytest.raises(Invalid, match="credentials"):
        export_profile(k.store, pid, version_id=d["id"])
    d2 = k.store.create_draft(pid, actor="admin")
    k.store.update_draft(d2["id"], {"settings": {"httpProxy": "http://user:pw@proxy:1", "compaction": {"enabled": False},
                                                  "sessionDir": "/tmp/x"}}, actor="admin")
    with pytest.raises(Invalid, match="credentials"):                        # URL userinfo is a credential
        export_profile(k.store, pid, version_id=d2["id"])
    d3 = k.store.create_draft(pid, actor="admin")
    k.store.update_draft(d3["id"], {"settings": {"sessionDir": "/tmp/x", "compaction": {"enabled": False}}}, actor="admin")
    files = export_profile(k.store, pid, version_id=d3["id"])
    kernos = json.loads(files["kernos.json"])
    assert kernos["settings"] == {"compaction": {"enabled": False}}
    assert json.loads(files[".pi/settings.json"])["compaction"] == {"enabled": False}
    assert secret_paths({"a": {"token": "t"}, "b": ["sk-123456789xyz"], "c": "fine"}) == ["a.token", "b[0]"]


def test_import_round_trips_into_a_fresh_business_as_sources_and_a_draft(db):
    k = kernel_for(db)
    files = export_profile(k.store, k.seed_report["profile_id"])
    fresh = k.store.create_business("copy", "Copy")
    out = import_package(k.store, fresh["id"], files, actor="admin")
    kinds = {(s["kind"], s["slug"]) for s in out["sources"]}
    assert ("rule", "money-safety") in kinds and ("skill", "record-meal") in kinds and ("prompt", "system") in kinds
    assert all(s["replaced"] is False for s in out["sources"]) and out["ignored"] == []
    assert any("must be registered" in w for w in out["warnings"])
    src = k.store.get_source(fresh["id"], "rule", "money-safety")
    assert src["frontmatter"]["tags"] == ["money"] and src["updated_by"] == "admin"          # the source keeps the tag (F4)
    assert k.store.get_source(fresh["id"], "skill", "record-meal")["frontmatter"]["delivery"] == "inline"
    draft = out["draft"]
    assert draft["status"] == "draft" and k.store.get_version(draft["id"])["spec"] == json.loads(files["kernos.json"])
    profile = k.store.get_profile(draft["profile_id"])
    assert profile["business_id"] == fresh["id"] and profile["published_version_id"] is None      # never published
    # a second import collides unless replace
    with pytest.raises(Conflict, match="replace=true"):
        import_package(k.store, fresh["id"], files, actor="admin")
    again = import_package(k.store, fresh["id"], files, actor="admin", replace=True)
    assert all(s["replaced"] for s in again["sources"])
    # refusals
    with pytest.raises(Invalid, match="agent may not import"):
        import_package(k.store, fresh["id"], files, actor="agent:phoenix")
    with pytest.raises(Invalid, match="refused path"):
        import_package(k.store, fresh["id"], {"../evil.md": b"x"}, actor="admin")
    with pytest.raises(Invalid, match="does not validate"):
        import_package(k.store, fresh["id"], {"kernos.json": b'{"bogus": 1}'}, actor="admin")
    with pytest.raises(Invalid, match="too large"):
        import_package(k.store, fresh["id"], {"AGENTS.md": b"x" * (8 * 1024 * 1024 + 1)}, actor="admin")


def test_a_stock_pi_package_becomes_sources_only(db):
    k = kernel_for(db)
    fresh = k.store.create_business("pi", "Pi")
    files = {
        "package.json": b'{"name": "x", "pi": {"skills": ["./skills"]}}',
        "skills/review/SKILL.md": b"---\nname: review\ndescription: Review staged changes\n---\n# Review\n\nLook at the diff.",
        "skills/nested/deeper/SKILL.md": b"---\ndescription: d\n---\nbody",
        "prompts/summarise.md": b"---\ndescription: Summarise\n---\nSummarise the thread.",
        "AGENTS.md": b"You are a careful reviewer.",
        "extensions/hook.ts": b"export default () => {}",
        "themes/dark.json": b"{}",
    }
    out = import_package(k.store, fresh["id"], files, actor="admin")
    assert out["draft"] is None and out["warnings"] == []
    assert sorted(out["ignored"]) == ["extensions/hook.ts", "themes/dark.json"]
    assert {(s["kind"], s["slug"]) for s in out["sources"]} == {("skill", "review"), ("skill", "deeper"), ("template", "summarise"), ("prompt", "system")}
    review = k.store.get_source(fresh["id"], "skill", "review")
    assert review["body"].startswith("# Review") and review["frontmatter"] == {"description": "Review staged changes", "delivery": "inline"}
    assert k.store.get_source(fresh["id"], "prompt", "system")["body"] == "You are a careful reviewer."
    with pytest.raises(Invalid, match="not a valid slug"):
        sources_from_pi_files({"skills/Bad Name/SKILL.md": b"---\nname: Bad Name\n---\nx"})
    assert parse_frontmatter("no front matter") == ({}, "no front matter")
    assert parse_frontmatter('---\ntags: [money, x]\ntitle: "a: b"\n---\nbody') == ({"tags": ["money", "x"], "title": "a: b"}, "body")
