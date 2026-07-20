"""Cursor SDK model-wiring for the sample Cursor agent.

This is the model-selection layer for the Cursor-SDK-backed agent. Its job is
to turn a plain model name (``"composer-2.5"``, ``"composer-2.5-fast"``,
``"auto"``) into a fully-resolved :class:`ModelSelection` carrying the model's
**variant params**.

WHY THIS EXISTS — parameterized models fail when sent bare. Models that declare
a parameter (e.g. ``composer-2.5`` / ``composer-2`` expose a ``fast`` param)
return an opaque ``RUN_LIFECYCLE_STATUS_ERROR`` (empty message, no generation)
if you pass ``model="composer-2.5"`` as a bare string. They must be sent as
``ModelSelection(id="composer-2.5", params=[ModelParameterValue(id="fast",
value="false")])``, resolved from ``Cursor.models.list()``. Models with no
params (``default``, ``gemini-*``) work bare. So: ALWAYS route a model name
through :func:`resolve_model_selection` before handing it to ``Agent.create`` /
``agent.send`` — never pass a bare parameterized id.
"""

from __future__ import annotations

import json
import logging

from cursor_sdk import (
    AuthenticationError,
    Cursor,
    CursorAgentError,
    ModelParameterValue,
    ModelSelection,
)
from cursor_sdk.types import ModelVariant, SDKModel

from app.config import settings

logger = logging.getLogger(__name__)

_cached_models_for_key: tuple[str, list[SDKModel]] | None = None
_cached_aliases_for_key: tuple[str, dict[str, str]] | None = None

# Base URL of the Cursor REST API. Overridable for tests / self-hosted gateways.
_CURSOR_API_BASE = settings.cursor_api_base

# Suffixes a caller can append to a model name to pin a variant explicitly.
MODEL_SUFFIX_OVERRIDES: list[tuple[str, list[ModelParameterValue]]] = [
    ("-no-fast", [ModelParameterValue(id="fast", value="false")]),
    ("-fast", [ModelParameterValue(id="fast", value="true")]),
]


class CursorRunnerError(Exception):
    """Configuration or transport failure before/during a Cursor SDK call."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.message = message
        if cause is not None:
            self.__cause__ = cause


class CursorRunnerRunStatusError(CursorRunnerError):
    """``run.wait()`` resolved with ``status == "error"``; eligible for retries."""


def resolve_cursor_api_key(api_key: str | None = None) -> str:
    """Single-key resolution: explicit arg > CURSOR_API_KEY env."""
    if api_key and api_key.strip():
        return api_key.strip()
    key = settings.cursor_api_key
    if not key:
        raise CursorRunnerError(
            "Missing Cursor SDK API key. Set CURSOR_API_KEY (a user/service-account "
            "key from the Cursor dashboard; not a Team Admin key)."
        )
    return key


def default_cursor_model() -> str:
    return settings.cursor_model


def agent_turn_budget() -> tuple[int, int]:
    return (settings.max_tools, settings.max_seconds)


# --------------------------------------------------------------------------- #
# Model resolution (the load-bearing part).
# --------------------------------------------------------------------------- #


def _list_models(api_key: str) -> list[SDKModel]:
    global _cached_models_for_key
    if _cached_models_for_key is not None and _cached_models_for_key[0] == api_key:
        return _cached_models_for_key[1]
    models = Cursor.models.list(api_key=api_key)
    _cached_models_for_key = (api_key, models)
    return models


def _find_model_by_name(models: list[SDKModel], name: str) -> SDKModel | None:
    needle = name.strip().lower()
    for model in models:
        if model.id.lower() == needle or model.display_name.lower() == needle:
            return model
    return None


def _build_alias_index(items: list[dict]) -> dict[str, str]:
    """Build a lowercased ``alias -> model id`` map from raw ``/v1/models`` items.

    First claimant wins on a duplicate alias (e.g. "gpt" is claimed by gpt-5.5,
    gpt-5.4, gpt-5.2, gpt-5.1 — list order, so gpt-5.5), matching the TS SDK's
    ``findModelByName`` which uses ``Array.find()``. Blank aliases and items with
    no ``id`` are skipped.
    """
    index: dict[str, str] = {}
    for item in items:
        model_id = item.get("id")
        if not model_id:
            continue
        for alias in item.get("aliases", []) or []:
            if isinstance(alias, str) and alias.strip():
                index.setdefault(alias.strip().lower(), model_id)
    return index


def _alias_index(api_key: str) -> dict[str, str]:
    """Map lowercased model alias -> canonical model id, from the Cursor REST catalog.

    WHY THIS EXISTS — cursor_sdk's ``SDKModel`` discards the ``aliases`` field that
    the Cursor ``/v1/models`` endpoint returns, so ``Cursor.models.list()`` (and thus
    :func:`_find_model_by_name`, which matches only id/display_name) cannot resolve a
    name like ``opus-4-8`` — an *alias* of ``claude-opus-4-8`` (display name "Opus
    4.8"). The TS SDK's ``findModelByName`` matches aliases and resolves it fine. We
    recover parity by fetching the raw catalog once per key to build the alias→id map.

    Best-effort: any failure (network, auth, parse) yields an empty map and is NOT
    cached, so callers degrade to ``auto`` this call but retry the fetch next time.
    """
    global _cached_aliases_for_key
    if _cached_aliases_for_key is not None and _cached_aliases_for_key[0] == api_key:
        return _cached_aliases_for_key[1]
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{_CURSOR_API_BASE}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.load(resp)
        index = _build_alias_index(payload.get("items", []))
        _cached_aliases_for_key = (api_key, index)  # cache only on a successful fetch
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, never block resolution
        logger.warning("[CursorRunner] alias catalog fetch failed; aliases unavailable (%s)", exc)
        index = {}
    return index


def _parse_model_specifier(name: str) -> tuple[str, list[ModelParameterValue] | None]:
    trimmed = name.strip()
    lower = trimmed.lower()
    for suffix, overrides in MODEL_SUFFIX_OVERRIDES:
        if lower.endswith(suffix):
            return trimmed[: -len(suffix)], overrides
    return trimmed, None


def _default_variant(item: SDKModel) -> ModelVariant | None:
    variants = item.variants
    if not variants:
        return None
    for variant in variants:
        if variant.is_default:
            return variant
    return variants[0]


def _non_fast_variant(item: SDKModel) -> ModelVariant | None:
    for variant in item.variants:
        if any(p.id == "fast" and p.value == "false" for p in variant.params):
            return variant
    return None


def _variant_for_bare_model_id(item: SDKModel) -> ModelVariant | None:
    return _non_fast_variant(item) or _default_variant(item)


def _variant_matches_overrides(variant: ModelVariant, overrides: list[ModelParameterValue]) -> bool:
    return all(
        any(p.id == o.id and p.value == o.value for p in variant.params) for o in overrides
    )


def _overrides_allowed_by_model(item: SDKModel, overrides: list[ModelParameterValue]) -> bool:
    return all(
        any(
            definition.id == o.id and any(v.value == o.value for v in definition.values)
            for definition in item.parameters
        )
        for o in overrides
    )


def _find_variant_for_overrides(item: SDKModel, overrides: list[ModelParameterValue]) -> ModelVariant | None:
    for variant in item.variants:
        if _variant_matches_overrides(variant, overrides):
            return variant
    return None


def _format_param_overrides(params: list[ModelParameterValue] | None) -> str:
    if not params:
        return ""
    return ",".join(f"{p.id}={p.value}" for p in params)


def model_list_item_to_selection(
    item: SDKModel,
    param_overrides: list[ModelParameterValue] | None = None,
    requested_label: str | None = None,
) -> ModelSelection:
    if param_overrides:
        variant = _find_variant_for_overrides(item, param_overrides)
        if variant:
            return ModelSelection(id=item.id, params=list(variant.params))

        fallback = _non_fast_variant(item) or _default_variant(item)
        if fallback and fallback.params:
            logger.warning(
                '[CursorRunner] "%s": variant %s not available for %s; using %s',
                requested_label or item.id,
                _format_param_overrides(param_overrides),
                item.id,
                _format_param_overrides(list(fallback.params)),
            )
            return ModelSelection(id=item.id, params=list(fallback.params))

        if _overrides_allowed_by_model(item, param_overrides):
            return ModelSelection(id=item.id, params=list(param_overrides))

    variant = _variant_for_bare_model_id(item)
    if variant and variant.params:
        return ModelSelection(id=item.id, params=list(variant.params))
    return ModelSelection(id=item.id)


# The chat UI sends only two reasoning levels: "medium" (balanced) and "xhigh"
# (the highest effort the chosen model supports). Map them onto whichever param
# the model actually exposes — different families name it differently:
#   GPT-5.x / codex → "reasoning"  (none|low|medium|high|extra-high|xhigh)
#   Claude opus/sonnet → "effort"  (low|medium|high|xhigh|max) + a "thinking" toggle
#   composer / gemini / grok / kimi → none (reasoning is a no-op, left at default)
_UI_REASONING_LEVELS = {"medium", "xhigh"}

# Ascending strength across the per-family value vocabularies, so "xhigh" (the
# UI's max-effort level) resolves to the model's top available value. xhigh and
# extra-high are the same tier under different family names; Claude's "max" tops out.
_EFFORT_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "extra-high": 4, "max": 5}


def _reasoning_param_id(item: SDKModel) -> str | None:
    """The param id carrying reasoning effort for this model, or None if it has none."""
    ids = {d.id for d in (getattr(item, "parameters", []) or [])}
    if "reasoning" in ids:
        return "reasoning"
    if "effort" in ids:
        return "effort"
    return None


def _allowed_param_values(item: SDKModel, param_id: str) -> list[str]:
    for d in getattr(item, "parameters", []) or []:
        if d.id == param_id:
            return [v.value for v in d.values]
    return []


def _reasoning_overrides(item: SDKModel, reasoning: str | None) -> list[ModelParameterValue]:
    """Translate a UI reasoning level into this model's param override(s).

    Returns ``[]`` when the level is blank/unrecognized or the model exposes no
    reasoning param (composer-2.5, gemini, …) — the model then runs at its default.
    For Claude (``effort``), ``thinking=true`` is paired in so the effort takes hold.
    """
    level = (reasoning or "").strip().lower()
    if level not in _UI_REASONING_LEVELS:
        return []
    param_id = _reasoning_param_id(item)
    if param_id is None:
        return []
    allowed = _allowed_param_values(item, param_id)
    if level == "medium":
        if "medium" not in allowed:
            return []
        value = "medium"
    else:  # "xhigh" → the model's highest available effort value
        ranked = [v for v in allowed if v in _EFFORT_RANK]
        if not ranked:
            return []
        value = max(ranked, key=lambda v: _EFFORT_RANK[v])
    overrides = [ModelParameterValue(id=param_id, value=value)]
    if param_id == "effort" and "thinking" in {d.id for d in (item.parameters or [])}:
        overrides.append(ModelParameterValue(id="thinking", value="true"))
    return overrides


def resolve_model_selection(api_key: str, model: str, reasoning: str | None = None) -> ModelSelection:
    """Turn a model name (+ optional UI reasoning level) into a ModelSelection.

    The single function the rest of the agent should call. Falls back to
    ``auto`` for unknown/blank names. ``reasoning`` ("medium"|"xhigh") is applied
    only when the resolved model exposes a reasoning/effort param; otherwise it is
    silently ignored (e.g. composer-2.5, gemini have no such param).
    """
    trimmed = model.strip()
    if not trimmed or trimmed.lower() == "auto":
        return ModelSelection(id="auto")

    models = _list_models(api_key)
    base_name, param_overrides = _parse_model_specifier(trimmed)

    item = _find_model_by_name(models, trimmed) or _find_model_by_name(models, base_name)
    if item is None:
        # The SDK drops model aliases; resolve them ourselves so names like
        # "opus-4-8" reach claude-opus-4-8 (see _alias_index). Mirrors the TS SDK.
        aliases = _alias_index(api_key)
        canonical = aliases.get(trimmed.lower()) or aliases.get(base_name.lower())
        if canonical:
            item = _find_model_by_name(models, canonical)
    if item is None:
        logger.warning('[CursorRunner] Unknown model "%s"; falling back to auto', trimmed)
        return ModelSelection(id="auto")

    combined = (param_overrides or []) + _reasoning_overrides(item, reasoning)
    selection = model_list_item_to_selection(item, combined or None, trimmed)
    logger.info(
        '[CursorRunner] Resolved model "%s" -> id=%s %s',
        trimmed,
        selection.id,
        _format_param_overrides(list(selection.params) if selection.params else None) or "(no params)",
    )
    return selection


# --------------------------------------------------------------------------- #
# Error formatting.
# --------------------------------------------------------------------------- #


def format_cursor_agent_failure(err: BaseException) -> str:
    if isinstance(err, CursorAgentError):
        parts = [f"{err.__class__.__name__}: {err.message}"]
        if err.code:
            parts.append(f"code={err.code}")
        if err.status is not None:
            parts.append(f"http={err.status}")
        if err.request_id:
            parts.append(f"requestId={err.request_id}")
        if err.is_retryable:
            parts.append("retryable=true")
        if isinstance(err, AuthenticationError) or err.status == 401:
            parts.append(
                "(401) Confirm CURSOR_API_KEY is valid, not revoked, copied without "
                "whitespace, and from Dashboard -> Integrations (user/service-account key; "
                "not Team Admin)."
            )
        return " | ".join(parts)
    if isinstance(err, Exception):
        return f"{err.__class__.__name__}: {err}"
    return str(err)
