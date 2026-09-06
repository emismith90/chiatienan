"""Agent capabilities (design §8.3, Phase 8 review F3/F9/F13): what an agent may do
to the CMS, validated as content and read with defaults that fail closed.

``{"cms": [verbs], "self_change_scope": [paths], "max_eval_runs_per_day": n}`` — verbs
from :data:`CMS_VERBS`, paths from :data:`SCOPE_VOCABULARY` (the blacklist is not in the
vocabulary, so it can never be granted), the run cap 0–10. An absent block means no verb,
no scope, and the default cap.
"""
from __future__ import annotations

from kernos.content.errors import Invalid

CMS_VERBS = ("read", "draft", "eval", "publish")
#: What an agent may publish to its own profile without a human (review F13 cut
#: `persona`, `memory`, `templates`: no consumer in the steward brief).
SCOPE_VOCABULARY = ("prompt.body", "prompt.append", "skills", "rules", "validation.warn")
DEFAULT_MAX_EVAL_RUNS_PER_DAY = 2
MAX_EVAL_RUNS_CAP = 10
_KEYS = ("cms", "self_change_scope", "max_eval_runs_per_day")


def normalise_capabilities(caps) -> dict:
    """Validate and normalise a stored ``capabilities`` value; raises ``Invalid``."""
    if caps is None:
        return {}
    if not isinstance(caps, dict):
        raise Invalid("capabilities must be an object")
    unknown = sorted(set(caps) - set(_KEYS))
    if unknown:
        raise Invalid(f"unknown capabilities keys {unknown}; known: {list(_KEYS)}")
    out: dict = {}
    if "cms" in caps:
        verbs = caps["cms"]
        if not isinstance(verbs, list) or any(v not in CMS_VERBS for v in verbs):
            raise Invalid(f"capabilities.cms must be a list of {list(CMS_VERBS)}")
        out["cms"] = [v for v in CMS_VERBS if v in verbs]
    if "self_change_scope" in caps:
        scope = caps["self_change_scope"]
        if not isinstance(scope, list) or any(p not in SCOPE_VOCABULARY for p in scope):
            raise Invalid(f"capabilities.self_change_scope must be a list of {list(SCOPE_VOCABULARY)}")
        out["self_change_scope"] = [p for p in SCOPE_VOCABULARY if p in scope]
    if "max_eval_runs_per_day" in caps:
        n = caps["max_eval_runs_per_day"]
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_EVAL_RUNS_CAP:
            raise Invalid(f"capabilities.max_eval_runs_per_day must be an integer 0–{MAX_EVAL_RUNS_CAP}")
        out["max_eval_runs_per_day"] = n
    return out


def agent_capabilities(agent: dict | None) -> dict:
    """``{"cms": set, "scope": list, "max_eval_runs_per_day": int}`` with fail-closed defaults."""
    caps = (agent or {}).get("capabilities") or {}
    return {"cms": set(caps.get("cms") or []),
            "scope": list(caps.get("self_change_scope") or []),
            "max_eval_runs_per_day": caps.get("max_eval_runs_per_day", DEFAULT_MAX_EVAL_RUNS_PER_DAY)}
