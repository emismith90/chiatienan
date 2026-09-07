"""A profile as a Pi package, and a Pi package as content (design §2 "Package", §12;
plan Task 9.2; Phase 9 review F4–F7).

**Export** writes what stock ``pi`` reads from a local-path package — ``skills/<slug>/
SKILL.md``, ``prompts/<slug>.md`` — plus the two files ``pi`` reads from a project:
``AGENTS.md`` (the system prompt and every rule, as a context file) and ``.pi/settings.json``
(model, thinking, compaction — an allowlist). ``kernos.json`` carries the whole stored
spec so a kernos import is lossless (pipeline, packs, validation, eval are not Pi
concepts). Nothing secret travels: ``runtime`` is never stored, the path and proxy
settings are dropped, and a string that looks like a key anywhere under ``settings``,
``extensions`` or ``meta`` refuses the export.

**Import** creates **sources** (the business's upstream content, snapshotted into every
future draft) and, when ``kernos.json`` is present, a **draft** version — never a publish:
the gates are the import's reviewer. With ``kernos.json`` the sources come from the spec
(rules keep their ``tags``, skills their ``delivery``); the Pi files exist for stock pi and
are ignored. Existing sources are not overwritten unless ``replace=True``.
"""
from __future__ import annotations

import json
import re
from typing import Any

from kernos.content.errors import Conflict, Invalid, NotFound
from kernos.content.spec import ProfileSpec
from kernos.eval.case import spec_sha
from pydantic import ValidationError

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PI_SKILL_RE = re.compile(r"[^a-z0-9-]+")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|\bapi[_-]?key\b|\btoken\b|\bsecret\b|\bpassword\b|://[^/\s:@]+:[^/\s@]+@)",
                       re.IGNORECASE)
#: `Settings` keys that are paths, proxies or commands: never exported (F6).
PATH_SETTINGS = ("httpProxy", "sessionDir", "shellPath", "shellCommandPrefix", "npmCommand")
#: `Settings` keys `.pi/settings.json` may carry (F6).
SETTINGS_ALLOW = ("defaultThinkingLevel", "thinkingBudgets", "compaction", "retry", "steeringMode")
ALLOWED_PATHS = ("package.json", "README.md", "kernos.json", "AGENTS.md", ".pi/settings.json")
MAX_TOTAL_BYTES = 8 * 1024 * 1024
RULE_HEADING = "## Rule: "


# ------------------------------------------------------------------- helpers

def _frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, str) and (':' in v or v != v.strip()) else v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """A minimal ``key: value`` / ``key: [a, b]`` front matter reader (no YAML dependency)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n")
    end = next((i for i in range(1, len(parts)) if parts[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict = {}
    for line in parts[1:end]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k.strip()] = [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
        elif len(v) >= 2 and v[0] == v[-1] == '"':
            meta[k.strip()] = json.loads(v)
        else:
            meta[k.strip()] = v
    return meta, "\n".join(parts[end + 1:]).lstrip("\n")


def pi_skill_name(name: str) -> str:
    slug = PI_SKILL_RE.sub("-", name.lower()).strip("-")[:64]
    return slug or "skill"


def _description(skill: dict) -> str:
    if skill.get("description"):
        return skill["description"]
    first = next((ln.strip().lstrip("# ").strip() for ln in (skill.get("body") or "").splitlines() if ln.strip()), "")
    return (first or skill["name"])[:200]


def secret_paths(obj: Any, path: str = "") -> list[str]:
    """Every path whose string value looks like a credential."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and SECRET_RE.search(v):
                out.append(here)
            elif isinstance(k, str) and SECRET_RE.search(k) and v not in (None, "", False):
                out.append(here)
            out += secret_paths(v, here) if isinstance(v, (dict, list)) else []
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            here = f"{path}[{i}]"
            if isinstance(v, str) and SECRET_RE.search(v):
                out.append(here)
            out += secret_paths(v, here) if isinstance(v, (dict, list)) else []
    return out


# -------------------------------------------------------------------- export

def export_profile(store, profile_id: int, *, version_id: int | None = None) -> dict[str, bytes]:
    profile = store.get_profile(profile_id)
    business = store.get_business(profile["business_id"])
    vid = version_id or profile.get("published_version_id")
    if vid is None:
        raise Invalid(f"profile {profile_id} has no published version to export")
    version = store.get_version(vid)
    if version["profile_id"] != profile_id:
        raise Invalid(f"version {vid} is not a version of profile {profile_id}")
    spec = dict(version["spec"])
    found = [p for root in ("settings", "extensions", "meta") for p in secret_paths(spec.get(root), root)]
    if found:
        raise Invalid(f"export refused: {found} look like credentials")
    settings = dict(spec.get("settings") or {})
    for key in PATH_SETTINGS:
        settings.pop(key, None)
    spec["settings"] = settings
    files: dict[str, bytes] = {}

    def put(path: str, text: str) -> None:
        files[path] = text.encode("utf-8")

    parsed = ProfileSpec.model_validate(spec)
    slug_of = {s["name"]: pi_skill_name(s["name"]) for s in spec.get("skills", [])}
    put("package.json", json.dumps({
        "name": f"kernos-{business['slug']}-{profile['name']}".lower().replace(" ", "-"),
        "version": f"{version['version']}.0.0", "private": True, "keywords": ["pi-package"],
        "pi": {"skills": ["./skills"], "prompts": ["./prompts"]},
        "kernos": {"business": business["slug"], "profile": profile["name"], "profile_id": profile_id,
                   "version": version["version"], "spec_sha": spec_sha(parsed)},
    }, indent=2, ensure_ascii=False) + "\n")
    put("README.md", f"# {business['name']} — {profile['name']} v{version['version']}\n\n"
        "Exported from kernos. To run under stock pi: `cd` into this directory and start `pi -e .` — "
        "the skills and prompt templates load by package rules, `AGENTS.md` is read from the current "
        "directory as a context file, and `.pi/settings.json` applies once the project is trusted. "
        "`kernos.json` is the full profile for a kernos import (`POST /api/admin/businesses/{id}/import`).\n")
    for sk in spec.get("skills", []):
        put(f"skills/{slug_of[sk['name']]}/SKILL.md",
            _frontmatter({"name": slug_of[sk["name"]], "description": _description(sk)}) + (sk.get("body") or ""))
    for tpl in spec.get("templates", []):
        meta = {"description": tpl.get("description") or tpl["name"]}
        put(f"prompts/{pi_skill_name(tpl['name'])}.md", _frontmatter(meta) + (tpl.get("content") or ""))
    agents = [(spec.get("prompt") or {}).get("body") or ""]
    for rule in spec.get("rules", []):
        agents.append(f"\n{RULE_HEADING}{rule['slug']}\n\n{rule['content']}")
    put("AGENTS.md", "\n".join(agents).strip() + "\n")
    model = (spec.get("models") or {}).get("text") or ""
    pi_settings: dict = {}
    if "/" in model:
        pi_settings["defaultProvider"], pi_settings["defaultModel"] = model.split("/", 1)
    elif model:
        pi_settings["defaultModel"] = model
    thinking = (spec.get("models") or {}).get("thinking")
    if thinking:
        pi_settings["defaultThinkingLevel"] = thinking
    for key in SETTINGS_ALLOW:
        if key in settings and key not in pi_settings:
            pi_settings[key] = settings[key]
    put(".pi/settings.json", json.dumps(pi_settings, indent=2, ensure_ascii=False) + "\n")
    put("kernos.json", json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return files


# -------------------------------------------------------------------- import

def _check_path(path: str) -> None:
    if path.startswith(("/", "\\")) or "\\" in path or ".." in path.split("/") or not path.strip():
        raise Invalid(f"refused path {path!r}")


def _slug(raw: str, what: str) -> str:
    slug = raw.strip().lower()
    if not SLUG_RE.match(slug):
        raise Invalid(f"{what} {raw!r} is not a valid slug ({SLUG_RE.pattern})")
    return slug


def sources_from_spec(spec: dict) -> list[dict]:
    """The source rows a stored spec implies: rules with tags, skills with delivery,
    templates, the system prompt (F4)."""
    out = [{"kind": "rule", "slug": r["slug"], "title": r["slug"], "body": r["content"],
            "frontmatter": {"tags": list(r.get("tags") or [])}} for r in spec.get("rules", [])]
    out += [{"kind": "skill", "slug": s["name"], "title": s["name"], "body": s.get("body") or "",
             "frontmatter": {"description": s.get("description", ""), "delivery": s.get("delivery", "inline")}}
            for s in spec.get("skills", [])]
    out += [{"kind": "template", "slug": t["name"], "title": t["name"], "body": t.get("content") or "",
             "frontmatter": {"kind": t.get("kind", "template"), "description": t.get("description", "")}}
            for t in spec.get("templates", [])]
    body = (spec.get("prompt") or {}).get("body")
    if body:
        out.append({"kind": "prompt", "slug": "system", "title": "system", "body": body, "frontmatter": {}})
    return out


def sources_from_pi_files(files: dict[str, bytes]) -> tuple[list[dict], list[str]]:
    """Sources a stock pi package implies: ``skills/**/SKILL.md`` → skill (inline),
    ``prompts/*.md`` → template, ``AGENTS.md`` → the system prompt. Returns ``(sources,
    ignored paths)``."""
    out, ignored = [], []
    for path, raw in files.items():
        text = raw.decode("utf-8")
        parts = path.split("/")
        if parts[0] == "skills" and parts[-1] == "SKILL.md" and len(parts) >= 3:
            meta, body = parse_frontmatter(text)
            slug = _slug(str(meta.get("name") or parts[-2]), "skill")
            out.append({"kind": "skill", "slug": slug, "title": slug, "body": body,
                        "frontmatter": {"description": str(meta.get("description") or ""), "delivery": "inline"}})
        elif parts[0] == "prompts" and len(parts) == 2 and path.endswith(".md"):
            meta, body = parse_frontmatter(text)
            slug = _slug(parts[1][:-3], "prompt template")
            out.append({"kind": "template", "slug": slug, "title": slug, "body": body,
                        "frontmatter": {"kind": "template", "description": str(meta.get("description") or "")}})
        elif path == "AGENTS.md":
            out.append({"kind": "prompt", "slug": "system", "title": "system", "body": text, "frontmatter": {}})
        elif path in ALLOWED_PATHS:
            continue
        else:
            ignored.append(path)
    return out, ignored


def import_package(store, business_id: int, files: dict[str, bytes], *, actor: str, replace: bool = False) -> dict:
    if actor.startswith("agent:"):
        raise Invalid("an agent may not import content")
    total = 0
    for path, raw in files.items():
        _check_path(path)
        total += len(raw)
    if total > MAX_TOTAL_BYTES:
        raise Invalid(f"package too large ({total} bytes; the limit is {MAX_TOTAL_BYTES})")
    store.get_business(business_id)
    warnings: list[str] = []
    draft = None
    if "kernos.json" in files:
        try:
            spec = ProfileSpec.model_validate(json.loads(files["kernos.json"].decode("utf-8"))).stored()
        except (ValueError, ValidationError) as exc:
            raise Invalid(f"kernos.json does not validate: {str(exc).splitlines()[0]}") from exc
        sources = sources_from_spec(spec)
        ignored = [p for p in files if p not in ALLOWED_PATHS and not p.startswith(("skills/", "prompts/"))]
        for ext in spec.get("extensions") or []:
            warnings.append(f"extension {ext.get('id') if isinstance(ext, dict) else ext!r} must exist in this host's sidecar")
        for ref in spec.get("tool_packs") or []:
            warnings.append(f"pack {ref.get('pack')!r} must be registered in this host (gate 1 checks at publish)")
    else:
        sources, ignored = sources_from_pi_files(files)
        spec = None
    existing = {(r["kind"], r["slug"]): r for r in store.list_sources(business_id)}
    clashes = [f"{s['kind']}/{s['slug']}" for s in sources if (s["kind"], s["slug"]) in existing]
    if clashes and not replace:
        raise Conflict(f"sources exist: {clashes}; pass replace=true to overwrite them")
    written = []
    for s in sources:
        row = existing.get((s["kind"], s["slug"]))
        store.put_source(business_id, s["kind"], s["slug"], body=s["body"], title=s["title"],
                         frontmatter=s["frontmatter"], actor=actor, if_match=row["etag"] if row else None)
        written.append({"kind": s["kind"], "slug": s["slug"], "replaced": row is not None})
    if spec is not None:
        profile = next((p for p in store.list_profiles(business_id) if p["name"] == "default"), None)
        if profile is None:
            profile = store.create_profile(business_id, "default", actor=actor)
        draft = store.create_draft(profile["id"], actor=actor, base_spec=spec, snapshot=False,
                                   note="imported package; publish through the gates")
    return {"sources": written, "draft": draft, "ignored": sorted(ignored), "warnings": warnings}
