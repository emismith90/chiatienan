"""Server-side image-attachment validation for the Cursor agent (vision)."""
from __future__ import annotations

import logging

_IMAGE_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_IMAGE_MAX_COUNT = 4
_IMAGE_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_MAX_TOTAL_BYTES = 6 * 1024 * 1024

logger = logging.getLogger("sample-cursor-agent")


def sanitize_images(raw) -> list[dict[str, str]] | None:
    """Validate ``forwardedProps.images`` into a clean list of {data, mimeType}.

    Each item must be {"data": <base64>, "mimeType": <allowed image type>}.
    Drops a leading ``data:...;base64,`` prefix, rejects disallowed mime types,
    enforces per-image + total size caps and a max count. Returns ``None`` when
    nothing is usable so callers keep the plain-text send path.
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
