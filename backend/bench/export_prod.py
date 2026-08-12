"""Turn real production conversations into a benchmark corpus, safely.

    python -m bench.export_prod --room 1 --days 90 \
        --out bench/corpus/prod_conversations.json

**The highest-risk file in the benchmark.** The golden datasets cover the money
arithmetic; only prod covers how people actually talk to this bot — and prod
messages contain real names and real bank details. Three lines of defence, in
order of how much they are trusted:

1. **The corpus file is gitignored.** Nothing downstream needs the bodies in
   git — only on disk when `bench.run --corpus prod` executes. Commit the case
   ids, the derived expectations (pseudonyms and integers), and the file's
   SHA-256. That turns "a leak here is an unrecoverable privacy incident" into
   "a leak here cannot happen via git".
2. **`sanitize` is pure and CI-tested** (`tests/test_bench_sanitize.py`).
3. **A human reads the output** before it is used. The test suite is a net, not
   a substitute for looking; `--verify` re-greps the written file for every real
   secret it knows about.

### Why a key denylist is not enough

Bank details enter this system *through chat*. `add_member` and `update_member`
take `bank_code`, `account_number` and `account_holder` as **tool arguments**
(`tools.py:178-211`), so a real body reads

    @bot cập nhật stk của tôi 0071000123456 VCB NGUYEN VAN A

and `body` is exactly what the corpus keeps. So bodies also get digit runs of 8+
redacted, and names replaced on **word boundaries** — bare substring replacement
mangles Vietnamese, because "An" occurs inside the ubiquitous pronoun "anh".
An `account_holder` is an uppercase, de-diacriticized legal name that a
display-name map would never match, so holders are collected separately.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

#: Stripped wherever they appear, at any depth, in `attachments`.
DENY_KEYS = ("account_number", "account_holder", "bank_code", "invite_token",
             "pin", "qr_url", "data")

#: A run of 8+ digits is never a VND amount in this corpus — amounts are ≤7
#: digits or carry a `k`/`tr`/`đ` unit — but it is exactly the shape of a bank
#: account or a phone number.
_LONG_DIGITS = re.compile(r"(?<!\d)\d{8,}(?!\d)")

_REDACTED = "[REDACTED]"

#: A bot reply's attachment type → the tool that produced it. Derived from
#: `chat.render_bot_attachments` and the draft paths (`chat.py:304-319`, `:511`).
TOOL_BY_ATTACHMENT = {
    "expense_draft": "propose_meal",
    "payment_draft": "propose_payment",
    "settlement": "settle_period",
    "settle_blocked": "settle_period",
    "statement": "member_statement",
    "summary": "get_period_summary",
    "random_pick": "pick_random",
}


# --------------------------------------------------------------------------- #
# sanitize — the pure, tested part
# --------------------------------------------------------------------------- #

def _deaccent(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def build_name_map(members: list[dict]) -> tuple[dict[str, str], list[str]]:
    """`({real form: pseudonym}, [account holders])` from the members table.

    Every form a body might use gets an entry: `display_name`, `nickname`, and
    each alias. `account_holder` is returned separately because it is not a name
    anyone is called — it is a bank's uppercase, de-diacriticized rendering, and
    it is replaced by a marker rather than a pseudonym.
    """
    name_map: dict[str, str] = {}
    holders: list[str] = []
    for index, member in enumerate(members, start=1):
        pseudonym = f"A{index}"
        for form in (member.get("display_name"), member.get("nickname"),
                     *(member.get("aliases") or [])):
            if form:
                name_map[str(form)] = pseudonym
        holder = member.get("account_holder")
        if holder:
            holders.append(str(holder))
            # Both spellings, since a body may carry either.
            holders.append(_deaccent(str(holder)))
    return name_map, sorted(set(holders), key=len, reverse=True)


def _replace_names(text: str, name_map: dict[str, str], holders: list[str]) -> str:
    """Replace real names with pseudonyms, on word boundaries, longest first.

    Longest first so a two-word name is not shredded into a pseudonym plus a
    leftover word. Word boundaries because a bare substring replacement mangles
    Vietnamese — "An" sits inside "anh", which appears in nearly every message.
    """
    for holder in holders:
        text = re.sub(rf"(?<!\w){re.escape(holder)}(?!\w)", "[HOLDER]", text,
                      flags=re.IGNORECASE)
    for real in sorted(name_map, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(real)}(?!\w)", name_map[real], text,
                      flags=re.IGNORECASE)
    return text


def _clean_text(text: str, name_map: dict[str, str], holders: list[str]) -> str:
    return _replace_names(_LONG_DIGITS.sub(_REDACTED, text or ""), name_map, holders)


def _clean_value(value, name_map: dict[str, str], holders: list[str]):
    """Strip denylisted keys at any depth; pseudonymize every string in between."""
    if isinstance(value, dict):
        return {k: _clean_value(v, name_map, holders)
                for k, v in value.items() if k not in DENY_KEYS}
    if isinstance(value, list):
        return [_clean_value(v, name_map, holders) for v in value]
    if isinstance(value, str):
        return _clean_text(value, name_map, holders)
    return value


def sanitize(rows: list[dict], name_map: dict[str, str] | None = None,
             holders: list[str] | None = None) -> list[dict]:
    """Pseudonymize and redact conversation rows. Pure — never mutates `rows`.

    Emits `had_images` (a count) and drops the bytes: an image is unreproducible
    in a corpus, but *that there was one* decides whether a case's expectation
    can be trusted at all.
    """
    name_map = dict(name_map or {})
    holders = list(holders or [])
    out = []
    for row in rows:
        attachments = row.get("attachments") or {}
        if isinstance(attachments, str):          # the CSV column arrives as JSON
            attachments = json.loads(attachments or "{}")
        images = (attachments.get("images") or []) if isinstance(attachments, dict) else []
        clean = {
            "id": row.get("id"),
            "created_at": row.get("created_at"),
            "kind": row.get("kind"),
            "author": _clean_text(str(row.get("author") or ""), name_map, holders),
            "body": _clean_text(row.get("body") or "", name_map, holders),
            "attachments": _clean_value(attachments, name_map, holders),
            "had_images": len(images),
        }
        if isinstance(clean["attachments"], dict):
            clean["attachments"].pop("images", None)
        out.append(clean)
    return out


# --------------------------------------------------------------------------- #
# expectation bootstrap
# --------------------------------------------------------------------------- #

def _attachment_type(row: dict) -> str | None:
    attachments = row.get("attachments") or {}
    return attachments.get("type") if isinstance(attachments, dict) else None


def build_cases(rows: list[dict], *, image_lookback: int = 10, day: str | None = None) -> list[dict]:
    """Derive one case per `@bot` turn, from what production actually did.

    The bot's own reply row records the tool: its `attachments.type` maps
    one-to-one onto the tool that produced it. What is **not** derivable is any
    member reference — prod member ids mean nothing in a bench room and there is
    no reconstructable prod ledger — so a prod case is graded on tool selection,
    plus `total` where the reply carries one.

    A case is marked `review: true` when no expectation could be derived, and
    `bench.corpus` skips those until a human clears them.

    A case is **image-tainted** when any of the previous `image_lookback` rows
    carried an image: `chat.recent_images` attaches a bill from an *earlier*
    message, so a text-only row can still have been answered from a photo, and a
    total read off that photo is unreproducible. Tainted cases drop their `args`.
    """
    cases = []
    for index, row in enumerate(rows):
        if row.get("kind") != "user" or "@bot" not in (row.get("body") or ""):
            continue

        window = rows[max(0, index - image_lookback):index + 1]
        tainted = any((r.get("attachments") or {}).get("images") for r in window
                      if isinstance(r.get("attachments"), dict))

        reply = next((r for r in rows[index + 1:index + 4] if r.get("kind") == "bot"), None)
        kind = _attachment_type(reply) if reply else None
        tool = TOOL_BY_ATTACHMENT.get(kind) if kind else None

        case = {
            "id": f'p{row.get("id")}',
            "day": day or str(row.get("created_at") or "")[:10],
            "actor": row.get("author") or "A1",
            "message": row.get("body") or "",
            "had_images": bool(tainted),
            "expect": {},
        }
        if tool is None:
            # No reply, or a reply shape this code does not understand. Never
            # guess an expectation — a wrong one grades the next engine wrongly.
            case["review"] = True
            cases.append(case)
            continue

        case["expect"]["tools"] = [tool]
        total = (reply.get("attachments") or {}).get("bill_total")
        if tool == "propose_meal" and isinstance(total, int) and not tainted:
            case["expect"]["args"] = {"propose_meal": {"total": total}}
        cases.append(case)
    return cases


# --------------------------------------------------------------------------- #
# fetch + CLI
# --------------------------------------------------------------------------- #

def fetch_rows(base_url: str, room_id: int, days: int, debug_key: str) -> list[dict]:
    """Pull the read-only conversation CSV (`app/debug_api.py`, `X-Debug-Key`)."""
    url = f"{base_url.rstrip('/')}/internal/debug/conversation.csv?room_id={room_id}&days={days}"
    request = Request(url, headers={"X-Debug-Key": debug_key})
    with urlopen(request, timeout=60) as response:  # noqa: S310 — operator-supplied URL
        text = response.read().decode("utf-8")
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        row = dict(raw)
        row["id"] = int(row["id"]) if row.get("id") else None
        try:
            row["attachments"] = json.loads(row.get("attachments") or "{}")
        except json.JSONDecodeError:
            row["attachments"] = {}
        rows.append(row)
    return rows


def verify(path: Path, secrets: list[str]) -> list[str]:
    """Re-grep a written corpus for anything that must not be in it."""
    blob = path.read_text(encoding="utf-8")
    found = [s for s in secrets if s and s in blob]
    if "vietqr" in blob.lower():
        found.append("vietqr")
    for match in _LONG_DIGITS.findall(blob):
        found.append(f"digit run {match}")
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench.export_prod", description=__doc__)
    parser.add_argument("--room", type=int, required=True)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--members", type=Path, required=True,
                        help="JSON dump of the room's members table, for the name map")
    args = parser.parse_args(argv)

    import os
    debug_key = os.environ.get("DEBUG_KEY")
    if not debug_key:
        print("DEBUG_KEY is not set — see deploy/DEBUGGING.md", file=sys.stderr)
        return 2

    members = json.loads(args.members.read_text(encoding="utf-8"))
    name_map, holders = build_name_map(members)

    rows = fetch_rows(args.base_url, args.room, args.days, debug_key)
    clean = sanitize(rows, name_map=name_map, holders=holders)
    cases = build_cases(clean)

    pseudonyms = sorted(set(name_map.values()))
    payload = {
        "members": [{"key": p, "display_name": p, "nickname": p.lower()} for p in pseudonyms],
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    secrets = list(name_map) + holders
    leaks = verify(args.out, secrets)
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()

    print(f"wrote {args.out} ({len(cases)} cases, "
          f'{sum(1 for c in cases if c.get("review"))} flagged review)', file=sys.stderr)
    print(f"sha256 {digest}", file=sys.stderr)
    if leaks:
        print(f"LEAKS FOUND — do not use this file: {sorted(set(leaks))}", file=sys.stderr)
        return 1
    print("no known secret found in the output — now read it yourself", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
