"""Server-side image-attachment validation for the vision turn.

Ported from the reference sample's ``_sanitize_images``. Teams pasted-inline
images are downloaded by the worker (with the bot bearer token) and handed here
as ``{"data": <base64>, "mimeType": ...}``. This drops any ``data:`` prefix,
rejects disallowed types, and enforces per-image + total size caps so a giant
bill photo can't blow up the vision turn.
"""
from __future__ import annotations

import logging

_IMAGE_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_IMAGE_MAX_COUNT = 4
_IMAGE_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_MAX_TOTAL_BYTES = 6 * 1024 * 1024

logger = logging.getLogger("chiatienan")


def sanitize_images(raw) -> list[dict[str, str]] | None:
    """Validate raw image dicts into a clean ``[{data, mimeType}]`` or ``None``.

    Returns ``None`` when nothing is usable so callers keep the plain-text send
    path.
    """
    if not isinstance(raw, list):
        return None
    out: list[dict[str, str]] = []
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("mimeType") or "").strip().lower()
        data = item.get("data")
        if mime not in _IMAGE_ALLOWED_MIME or not isinstance(data, str) or not data:
            continue
        if data.startswith("data:"):
            _, _, data = data.partition(",")
        data = data.strip()
        if not data:
            continue
        size = (len(data) * 3) // 4
        if size > _IMAGE_MAX_BYTES or total + size > _IMAGE_MAX_TOTAL_BYTES:
            logger.warning("Dropping image attachment over size budget (mime=%s)", mime)
            continue
        out.append({"data": data, "mimeType": mime})
        total += size
        if len(out) >= _IMAGE_MAX_COUNT:
            break
    return out or None
