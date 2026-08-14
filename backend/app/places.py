"""Place identity + resolution, room-scoped.

Mirrors :mod:`app.roster`'s role for members, and deliberately stays a separate
module and a separate namespace: a place name must never resolve to a person
(design D18). ``roster._NameIndex`` searches **bank account holders**, so this
room's Nhím is reachable as "Trang" while the room eats at "Bún riêu cô Trang".
Keeping the indexes apart is what stops one being answered with the other.
"""
from __future__ import annotations

import re

from app.roster import _fold


def slugify(name: str) -> str:
    """``"Cơm gà Thịnh Lơ"`` -> ``"com-ga-thinh-lo"``.

    Delegates the hard part to :func:`roster._fold`, which already lowercases,
    strips Vietnamese tones, hand-maps ``đ`` (NFD leaves it whole) and squashes
    punctuation to spaces. This only joins the words.
    """
    return re.sub(r"\s+", "-", _fold(name)).strip("-")
