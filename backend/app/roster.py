"""Member roster: read + name/alias/mention resolution, room-scoped.

Rooms (design pivot: PWA replaces the Teams tenant) each carry their own
member list. Account creation/administration lives in ``accounts.py`` (a
later task); this module only lists and resolves the members of a given
room — no Teams identity capture, no ``teams_user_id`` / ``aad_object_id``.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member


def list_members(
    session: Session, room_id: int, *, include_inactive: bool = False, default_only: bool = False
) -> list[Member]:
    """Members of ``room_id``, ordered by display name.

    Excludes soft-deleted (``active=False``) members by default so they drop out
    of the roster, mentions, and new-meal selection. Pass
    ``include_inactive=True`` for name-resolution over historical data (past
    balances/settlements can still reference a since-removed member).

    ``default_only=True`` further restricts to ``default_participant=True``
    members — the ones swept into a "whole group" chia tien split or rot tra
    draw without being named explicitly (design: exclude irregular members by
    default; they're still resolvable by name).
    """
    stmt = select(Member).where(Member.room_id == room_id)
    if not include_inactive:
        stmt = stmt.where(Member.active.is_(True))
    if default_only:
        stmt = stmt.where(Member.default_participant.is_(True))
    return list(session.scalars(stmt.order_by(Member.display_name)))


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _fold(s: str) -> str:
    """Lowercase, strip Vietnamese tones, and squash punctuation to spaces.

    "Hưng" and "Hung" are the same person written two ways — one on the roster,
    one in the bank-account holder line, and either can be the one someone types.
    NFD does not decompose ``đ``, so that pair is mapped by hand.
    """
    s = unicodedata.normalize("NFD", (s or "").strip().lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d")  # đ (NFD leaves it whole)
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    return _fold(s).split()


# Kinship terms Vietnamese puts in front of a name ("anh Hưng", "chị Nhím", and
# the "a Hưng" / "c Nhím" chat shorthand). Stripped only as a *fallback*, after
# the whole string has failed to match, so a member actually called "Cô" or "Bà"
# is still found by their own name.
_HONORIFICS = {
    "anh", "a", "chi", "c", "em", "e", "ban", "bac", "chu", "co", "ong", "ba",
    "cau", "di", "thim", "mo", "sep", "mr", "ms", "mrs",
}


def _strip_honorific(tokens: list[str]) -> list[str]:
    """Drop leading kinship terms, never the last token (that IS the name)."""
    i = 0
    while i < len(tokens) - 1 and tokens[i] in _HONORIFICS:
        i += 1
    return tokens[i:]


def name_tokens(s: str) -> set[str]:
    """The words of a name, tone-stripped, minus any leading kinship term."""
    return set(_strip_honorific(_tokens(s)))


class _NameIndex:
    """Every way a room member can be named, indexed for lookup.

    Searchable fields: ``display_name``, ``nickname``, every alias, and the
    **bank account holder** — the last is often the only place a person's real
    full name is written ("Le Hoang Hung" for the member displayed as "Emi"),
    and it is exactly the name colleagues use in chat.
    """

    def __init__(self, members: list[Member]):
        self.members = list(members)
        self.exact: dict[str, list[Member]] = {}       # accent-preserving
        self.folded: dict[str, list[Member]] = {}      # tone-stripped
        self.given: dict[str, list[Member]] = {}       # last token = Vietnamese given name
        self.token: dict[str, list[Member]] = {}       # any token, any position
        self.member_tokens: dict[int, set[str]] = {}
        for m in members:
            self.member_tokens[m.id] = set()
            for raw in self._fields(m):
                if not (raw or "").strip():
                    continue
                self._add(self.exact, _norm(raw), m)
                folded = _fold(raw)
                if not folded:
                    continue
                self._add(self.folded, folded, m)
                toks = folded.split()
                self.member_tokens[m.id].update(toks)
                self._add(self.given, toks[-1], m)
                for t in toks:
                    self._add(self.token, t, m)

    @staticmethod
    def _fields(m: Member) -> list[str]:
        return [m.display_name, m.nickname, m.account_holder, *(m.aliases or [])]

    @staticmethod
    def _add(index: dict[str, list[Member]], key: str, m: Member) -> None:
        bucket = index.setdefault(key, [])
        if m.id not in {x.id for x in bucket}:
            bucket.append(m)

    def lookup(self, raw: str) -> list[Member]:
        """Members ``raw`` could mean, best tier first; >1 = genuinely ambiguous.

        Tiers run narrow→broad and the first non-empty one wins: an exact hit is
        never widened into a token sweep, so "Trang" staying ambiguous between
        two people can't be papered over by a lucky substring.
        """
        toks = _tokens(raw)
        if not toks:
            return []
        stripped = _strip_honorific(toks)
        hit = (self.exact.get(_norm(raw))
               or self.folded.get(_fold(raw))
               or self.folded.get(" ".join(stripped)))
        if hit:
            return hit
        if len(stripped) > 1:
            # "Hưng Lê" / "Le Minh Duc": every word must be one of that member's
            # words, in any order — Vietnamese name order flips constantly.
            want = set(stripped)
            return [m for m in self.members if want <= self.member_tokens[m.id]]
        word = stripped[0]
        # Given name first: "Hoàng" is Giang Hoàng's name and only Le Hoang Hung's
        # middle word, so it is not ambiguous between them.
        return self.given.get(word) or self.token.get(word) or []


def member_name_tokens(session: Session, room_id: int) -> dict[int, set[str]]:
    """``{member_id: every word of every name they answer to}``, tone-stripped.

    Used to tell whether a name the room mentioned ended up represented in a
    proposal at all (see ``propose_meal``'s dropped-name guard).
    """
    return _NameIndex(list_members(session, room_id)).member_tokens


def resolve(
    session: Session,
    room_id: int,
    *,
    names: list[str] | None = None,
    mentions: list[dict] | None = None,
    all_active: bool = False,
) -> dict:
    """Resolve free-text names / ``@mentions`` (+ ``all_active``) into member ids
    within ``room_id``.

    ``mentions`` are ``{"nickname": ...}`` dicts resolved within the room. A
    name matches on ``display_name``, ``nickname``, any alias, **or the bank
    account holder**, tone-insensitively and by given name — see
    :class:`_NameIndex`. Returns ``{"matched": [{"id", "display_name"}],
    "unresolved": [<raw strings>], "ambiguous": [{"name", "candidates"}]}``.
    ``all_active=True`` returns every *default-participant* room member so the
    LLM never has to enumerate the roster from memory (design §5) — members
    flagged out of default group activities are skipped here but still match
    by an explicit name/mention below.

    A name that could be two people comes back under ``ambiguous`` rather than
    being guessed: picking one silently bills the wrong person, and the caller
    (the model) can just ask which one is meant.
    """
    index = _NameIndex(list_members(session, room_id))

    matched: dict[int, Member] = {}
    unresolved: list[str] = []
    ambiguous: list[dict] = []

    if all_active:
        for m in list_members(session, room_id, default_only=True):
            matched[m.id] = m

    def take(raw: str) -> None:
        hits = index.lookup(raw)
        if len(hits) == 1:
            matched[hits[0].id] = hits[0]
        elif hits:
            ambiguous.append({
                "name": raw,
                "candidates": [{"id": m.id, "display_name": m.display_name} for m in hits],
            })
        else:
            unresolved.append(raw)

    for mention in mentions or []:
        take(mention.get("nickname") or "?")

    for raw in names or []:
        take(raw)

    return {
        "matched": [{"id": m.id, "display_name": m.display_name} for m in matched.values()],
        "unresolved": unresolved,
        "ambiguous": ambiguous,
    }
