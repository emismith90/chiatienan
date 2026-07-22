"""Build the human-readable transfer note that rides along a settlement QR.

Pure, no I/O — like :mod:`app.money`, this text is deterministic and never
transcribed by the LLM. The note becomes the VietQR ``addInfo`` (and the stored
transfer note), so it must be ASCII (bank content fields drop diacritics) and
short (the ``addInfo`` character budget is tight).

Format: ``"<debtor>: <T2 dish>, <T3 dish>"`` — the debtor (the person paying via
the QR) followed by the meals they are repaying, one chunk per meal, capped at
``budget`` characters with a trailing ``+N`` for meals that don't fit.
"""
from __future__ import annotations

import unicodedata
from datetime import date

# Vietnamese weekday labels: Monday=thứ 2 ... Saturday=thứ 7, Sunday=Chủ Nhật.
_VN_WEEKDAYS = {0: "T2", 1: "T3", 2: "T4", 3: "T5", 4: "T6", 5: "T7", 6: "CN"}

_DEFAULT_BUDGET = 50


def _ascii_fold(s: str) -> str:
    """Strip Vietnamese diacritics to plain ASCII (``"bún chả" -> "bun cha"``).

    ``đ/Đ`` don't decompose under NFKD, so they're mapped explicitly. Any other
    non-ASCII survivor is dropped rather than mangled.
    """
    if not s:
        return ""
    s = s.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.encode("ascii", "ignore").decode("ascii")


def _weekday_label(d: date) -> str:
    return _VN_WEEKDAYS[d.weekday()]


def _meal_chunk(meal: dict) -> str:
    """One meal as ``"<weekday> <dish>"`` (weekday alone if the dish is blank)."""
    label = _weekday_label(meal["date"])
    dish = _ascii_fold((meal.get("dish") or "").strip())
    return f"{label} {dish}" if dish else label


def build_qr_note(
    debtor_name: str,
    meals: list[dict],
    *,
    fallback: str,
    budget: int = _DEFAULT_BUDGET,
) -> str:
    """The ASCII settlement note for a debtor repaying a set of ``meals``.

    ``meals`` are ``{"date": date, "dish": str | None}`` (sorted here by date, so
    the caller need not). Returns ``"<name>: T2 dish, T3 dish"`` trimmed to
    ``budget`` chars, collapsing any meals that don't fit into a trailing
    ``+N``. Returns ``fallback`` (ASCII-folded) when there are no meals — e.g. a
    transfer that comes purely from an ad-hoc payment.
    """
    if not meals:
        return _ascii_fold(fallback)

    ordered = sorted(meals, key=lambda m: m["date"])
    name = _ascii_fold(debtor_name or "").strip()
    prefix = f"{name}: " if name else ""
    chunks = [_meal_chunk(m) for m in ordered]

    full = prefix + ", ".join(chunks)
    if len(full) <= budget:
        return full

    # Doesn't fit — keep the most meals that leave room for a "+N" overflow tag.
    for keep in range(len(chunks) - 1, -1, -1):
        parts = chunks[:keep] + [f"+{len(chunks) - keep}"]
        candidate = prefix + ", ".join(parts)
        if len(candidate) <= budget:
            return candidate

    # Even the bare "+N" overruns (an unusually long name): best effort.
    return f"{prefix}+{len(chunks)}"
