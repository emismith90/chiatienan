"""Turn real production conversations into a benchmark corpus **and a baseline**.

    python -m bench.export_prod --room 3 --days 0 \
        --out bench/corpus/prod_conversations.json \
        --baseline bench/results/baseline-prod-cursor.json

### Why this file is also the baseline

The plan's Task 9 captures a Cursor baseline by re-running the Cursor engine. It
does not have to: **production has been running Cursor all along, so the
conversation log *is* a recorded Cursor run.** Every bot reply in it was produced
by the engine being replaced, against real messages, with the real prompt. Reading
it costs nothing and needs no Cursor credentials.

What that buys and what it does not:

* ✅ A reference drawn from **real traffic**, not authored cases — 108 turns
  against the golden corpora's 20, and the only source of `prose_quality`
  coverage worth having (`grade_prose` is n/a on 19 of the 20 golden cases).
* ⚠️ **One observation per case, not three.** A recorded log cannot be re-rolled,
  so the baseline side has `repeat=1`. Repeat *Pi* instead: a fixed reference with
  a repeated candidate still separates a real regression from sampling noise on
  the side that can vary.
* ⚠️ **The baseline passes its own expectations by construction.** Both are
  derived from the same log row, so its pass rate is 1.0 by definition and proves
  nothing about the graders. The number that means something is **Pi's absolute
  pass rate against prod's recorded behavior**. The emitted file is stamped
  `baseline_kind: "recorded-prod-log"` and `bench.report --compare` warns on it,
  so nobody reads "Cursor 100% → Pi 87%" as a 13-point regression measured two
  ways.

### The schema, as it actually is

Verified against room 3 (282 rows, 2026-07-22 → 08-06) rather than assumed:

* A user message has `kind="text"` — **not** `"user"`.
* A bot turn's output is `kind` ∈ `{"bot", "expense_draft", "payment_draft"}`;
  the two draft kinds are cards, and a card turn's prose is never posted.
* Attachment `type` values present: `payment`, `settlement`, `payment_draft`,
  `expense_draft`, `meal`, `statement`, `random_pick`, `settle_blocked`,
  `summary`. `meal` and `payment` are **commits** (a human pressed Confirm), not
  LLM turns.
* Cases are paired **backwards from bot replies** — nearest preceding `text` row —
  not forwards from messages containing `@bot`. A bare reply to a bot question
  triggers a turn too (`chat.replies_to_bot_question`), and pairing backwards
  catches those for free.

### Privacy

Three lines of defence, in order of how much they are trusted: the corpus file is
**gitignored**; `sanitize` is pure and CI-tested; and **a human reads the output**.
The third is not ceremony — it is what caught that the member name map misses
*non-member* people (a meal's `initiator`, a `guests` entry). Those are now
collected from the log's own attachments and pseudonymized as `G1…`, and
`residual_name_candidates` lists what still looks like a name so a reviewer has
somewhere to look.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

#: The droplet's debug key. `deploy/DEBUGGING.md` §6; the header is `X-Debug-Key`.
KEY_ENV = "DEBUG_API_KEY"

DEFAULT_BASE_URL = "https://chiatienan.duckdns.org"

#: `kind` of a human message. Not `"user"` — that guess found zero cases.
USER_KIND = "text"

#: `kind`s the bot writes. The two draft kinds are cards.
BOT_KINDS = ("bot", "expense_draft", "payment_draft")

#: Stripped wherever they appear, at any depth, in `attachments`.
DENY_KEYS = ("account_number", "account_holder", "bank_code", "invite_token",
             "pin", "qr_url", "data")

#: A run of 8+ digits is never a VND amount in this corpus — amounts are ≤7
#: digits or carry a `k`/`tr`/`đ` unit — but it is exactly the shape of a bank
#: account or a phone number.
_LONG_DIGITS = re.compile(r"(?<!\d)\d{8,}(?!\d)")

_REDACTED = "[REDACTED]"

#: A bot reply's attachment type → the tool that produced it, for the types that
#: represent an **LLM turn**. Derived from `chat.render_bot_attachments`
#: (`chat.py:304-319`) and the draft paths (`chat.py:511-538`).
TOOL_BY_ATTACHMENT = {
    "expense_draft": "propose_meal",
    "payment_draft": "propose_payment",
    "settlement": "settle_period",
    "settle_blocked": "settle_period",
    "statement": "member_statement",
    "summary": "get_period_summary",
    "random_pick": "pick_random",
}

#: Commits, not turns: a human pressed Confirm on a card. They are the ledger
#: history, so they seed a world rather than expect a tool.
COMMIT_TYPES = ("meal", "payment")


# --------------------------------------------------------------------------- #
# sanitize — the pure, tested part
# --------------------------------------------------------------------------- #

def _deaccent(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def collect_person_names(rows: list[dict]) -> list[str]:
    """Non-member humans the log names itself: meal `initiator`s and `guests`.

    The member table cannot supply these, and they are real people's names sitting
    in message bodies ("thứ 3 ăn bún bò huế, <name> rủ"). Reading the sanitized
    output is what surfaced them; this is the fix.
    """
    found: set[str] = set()
    for row in rows:
        attachments = row.get("attachments") or {}
        if not isinstance(attachments, dict):
            continue
        for guest in attachments.get("guests") or []:
            if isinstance(guest, str) and guest.strip():
                found.add(guest.strip())
        initiator = attachments.get("initiator")
        if isinstance(initiator, str) and initiator.strip():
            found.add(initiator.strip())
    return sorted(found, key=len, reverse=True)


#: Vietnamese kinship pronouns and particles that turn up *inside* display names
#: ("chị Linh"). Never mapped on their own — substituting a pseudonym for "anh"
#: would corrupt most messages in the room.
_NOT_A_NAME = frozenset(("anh", "chi", "chị", "em", "ban", "bạn", "ong", "ông",
                         "ba", "bà", "co", "cô", "chu", "chú", "bac", "bác",
                         "me", "mẹ", "bot"))

#: Shortest standalone token from a multi-word name that is worth mapping. Four,
#: not three: it catches "Hoang" and "Nhim" while sparing the three-letter
#: pronouns above.
_MIN_TOKEN = 4


def _name_forms(form: str) -> set[str]:
    """Every spelling of one name a message body might actually contain.

    Two expansions, both found by reading real output rather than by reasoning:

    * **ASCII-folded.** `notes.build_qr_note` folds the debtor's name before
      putting it in the QR memo (`notes.py:78`), and that memo is rendered into
      the bot's visible reply — so "Hoàng" reaches the corpus as "Hoang" and a map
      holding only the diacritic form sails past it.
    * **Per token.** A display name is often two words while a memo or a message
      uses one of them, so each token of length ≥4 maps to the same pseudonym.
    """
    forms = {form, _deaccent(form)}
    for variant in tuple(forms):
        for token in variant.split():
            if len(token) >= _MIN_TOKEN and token.lower() not in _NOT_A_NAME:
                forms.add(token)
    return {f for f in forms if len(f) >= 2 and f.lower() not in _NOT_A_NAME}


def build_name_map(members: list[dict],
                   guests: list[str] = ()) -> tuple[dict[str, str], list[str]]:
    """`({real form: pseudonym}, [account holders])`.

    Members become `A1…`, and every form a body might use gets an entry:
    `display_name`, `nickname`, each alias — each of those also ASCII-folded and
    split into tokens (see `_name_forms`). Non-member people become `G1…`.
    `account_holder` is returned separately because it is not a name anyone is
    called — it is a bank's uppercase, de-diacriticized rendering — and it is
    replaced by a marker rather than a pseudonym.
    """
    name_map: dict[str, str] = {}
    holders: list[str] = []
    for index, member in enumerate(members, start=1):
        pseudonym = f"A{index}"
        for form in (member.get("display_name"), member.get("nickname"),
                     *(member.get("aliases") or [])):
            if form:
                for spelling in _name_forms(str(form)):
                    name_map.setdefault(spelling, pseudonym)
        holder = member.get("account_holder")
        if holder and holder != "[redacted]":
            holders.append(str(holder))
            holders.append(_deaccent(str(holder)))
    for index, guest in enumerate(guests, start=1):
        for spelling in _name_forms(str(guest)):
            name_map.setdefault(spelling, f"G{index}")
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
    in a corpus, but *that there was one* decides whether a case's expectation can
    be trusted at all.
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
            "author_member_id": row.get("author_member_id"),
            "author": _clean_text(str(row.get("author") or ""), name_map, holders),
            "body": _clean_text(row.get("body") or "", name_map, holders),
            "attachments": _clean_value(attachments, name_map, holders),
            "had_images": len(images),
        }
        if isinstance(clean["attachments"], dict):
            clean["attachments"].pop("images", None)
        out.append(clean)
    return out


#: Word tokens, for the residual-name skim. Capitalization is decided with
#: `str.isupper()` rather than a character class: the obvious `[A-ZĐÀ-Ỹ]` range
#: spans U+00C0–U+1EF9 and so matches *lowercase* diacritics too, which buried the
#: review list under "được", "ăn" and "đã".
_WORD = re.compile(r"(?<![\w@])([^\W\d_][\w]*)")


def residual_name_candidates(clean_rows: list[dict], allowed: set[str]) -> dict[str, int]:
    """Capitalized tokens left in sanitized bodies, for a human to skim.

    The sanitizer can only redact names it was told about. This surfaces what it
    could not — a person mentioned once who never became a member, a guest, or an
    initiator. Deliberately noisy: place names, dish names and sentence-initial
    words all show up, and a reviewer dismissing them takes seconds while a missed
    name is a privacy incident.
    """
    counts: dict[str, int] = {}
    for row in clean_rows:
        for token in _WORD.findall(row.get("body") or ""):
            # Title-case only: an all-caps token is shouting, not a name, and a
            # lowercase one is a word.
            if len(token) < 2 or not token[0].isupper() or token.isupper():
                continue
            if token in allowed or token.lower() in allowed:
                continue
            counts[token] = counts.get(token, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- #
# cases and the recorded baseline
# --------------------------------------------------------------------------- #

def _attachment_type(row: dict) -> str | None:
    attachments = row.get("attachments") or {}
    return attachments.get("type") if isinstance(attachments, dict) else None


def _turns(rows: list[dict]) -> list[tuple[dict, list[dict]]]:
    """Pair each triggering user message with every bot row that answered it.

    Backwards from the bot's replies, because that is the unambiguous direction: a
    bot row exists iff a turn ran, whereas guessing which user messages *should*
    have triggered one means reimplementing `mentions_bot` **and**
    `replies_to_bot_question` and getting both right.
    """
    pairs: dict[int, tuple[dict, list[dict]]] = {}
    for index, row in enumerate(rows):
        if row.get("kind") not in BOT_KINDS:
            continue
        if _attachment_type(row) in COMMIT_TYPES:
            # A `meal` / `payment` row is a human pressing Confirm, not a turn the
            # LLM ran. Pairing one would invent a case out of whatever text row
            # happened to precede the button press.
            continue
        trigger = next((rows[j] for j in range(index - 1, -1, -1)
                        if rows[j].get("kind") == USER_KIND), None)
        if trigger is None:
            continue
        entry = pairs.setdefault(trigger["id"], (trigger, []))
        entry[1].append(row)
    return [pairs[k] for k in sorted(pairs)]


#: How many preceding messages a replayed prod case carries as history.
#:
#: Production passes `limit=settings.history_max_messages` (200) from the last
#: memory watermark, and **the watermark is unrecoverable from the log** — the
#: summarizer's position was never written to `room_messages`. So this is a
#: reconstruction, not the exact window prod used: bounded at 30 because that is
#: enough for the references these messages actually make ("log", "còn nợ ai",
#: "viết lại cho gọn") while keeping a 107-case replay affordable.
HISTORY_WINDOW = 30

#: `kind`s production feeds the model as history. `chat.build_history` filters
#: `kind.in_(("text", "bot"))`, so the two *draft* kinds are absent — a card's
#: contents never entered the model's history, only the prose that came with it.
HISTORY_KINDS = ("text", "bot")


def render_history(rows: list[dict], index: int, to_key, *,
                   window: int = HISTORY_WINDOW, clamp: int = 500) -> str:
    """The rows before `index`, rendered the way production renders history.

    Mirrors `chat._render_messages` exactly — `«Name»: body` for a member,
    `chiatienan: body` for the bot, `[ảnh: N]` for an image, oldest→newest, each
    body clamped — because a replay fed a *different* history is not a replay.

    **Without this the prod corpus was unanswerable in places, and graded the
    engine for it.** `p120`'s whole message is "@bot log"; `p129` is "tôi đã trả
    tiền A1" against an expectation of 27,000đ that appears nowhere in it; `p156`
    asks to reformat an answer given one turn earlier. Production had the
    conversation in front of it for all three. Replaying the message alone asks
    the model to guess, then records the guess as a tool-selection failure.
    """
    prior = [row for row in rows[:index] if row.get("kind") in HISTORY_KINDS]
    lines = []
    for row in prior[-window:]:
        body = (row.get("body") or "").strip()
        if len(body) > clamp:
            body = body[:clamp] + "…"
        count = int(row.get("had_images") or 0)
        if count:
            body = (f"{body} " if body else "") + f"[ảnh: {count}]"
        author_id = row.get("author_member_id")
        if author_id in (None, "", 0):
            lines.append(f"chiatienan: {body}")
        else:
            lines.append(f"«{to_key(author_id)}»: {body}")
    return "\n".join(lines)


def _money_args_from_attachment(tool: str, attachments: dict) -> dict | None:
    """The money the bot actually passed, read back out of what it produced.

    Member references stay **prod member ids** here; `build_cases` maps them to
    pseudonymous keys, which `bench.corpus.resolve_args` then maps to the ids of
    whatever room the replay builds.
    """
    if tool == "propose_meal":
        args = {"total": attachments.get("bill_total"),
                "payer": attachments.get("payer_member_id"),
                "participants": list(attachments.get("member_participants") or [])}
        if attachments.get("guests"):
            args["guests"] = list(attachments["guests"])
        if attachments.get("adjustments"):
            args["adjustments"] = [dict(a) for a in attachments["adjustments"]]
        if attachments.get("items"):
            args["items"] = [dict(i) for i in attachments["items"]]
        return args if args["total"] and args["participants"] else None
    if tool == "propose_payment":
        transfers = attachments.get("transfers") or []
        if len(transfers) != 1:
            # A multi-transfer card is several propose_payment calls; grading it
            # as one would compare the wrong pair.
            return None
        transfer = transfers[0]
        return {"from": transfer.get("from_member_id"), "to": transfer.get("to_member_id"),
                "amount": transfer.get("amount")}
    return None


def build_cases(rows: list[dict], *, image_lookback: int = 10,
                key_by_member_id: dict | None = None) -> list[dict]:
    """One case per recorded bot turn, expecting what production actually did.

    The bot's reply records the tool: its `attachments.type` maps one-to-one onto
    the tool that produced it. Money arguments are read back out of the produced
    payload and translated from prod member ids into pseudonymous keys.

    A case is marked `review: true` when no expectation could be derived, and
    `bench.corpus` skips those until a human clears them — never a guessed
    expectation, which grades the next engine wrongly while looking like data.

    A case is **image-tainted** when any of the previous `image_lookback` rows
    carried an image: `chat.recent_images` answers from an *earlier* message, so a
    text-only row can still have been answered from a photo, and a total read off
    a photo is unreproducible. Tainted cases keep the tool but drop `args`.
    """
    key_by_member_id = {str(k): v for k, v in (key_by_member_id or {}).items()}
    by_index = {row["id"]: i for i, row in enumerate(rows)}

    def to_key(member_id):
        return key_by_member_id.get(str(member_id), member_id)

    cases = []
    for trigger, replies in _turns(rows):
        index = by_index[trigger["id"]]
        window = rows[max(0, index - image_lookback):index + 1]
        tainted = any((r.get("attachments") or {}).get("images")
                      or r.get("had_images") for r in window
                      if isinstance(r.get("attachments"), dict))

        tool, payload = None, {}
        for reply in replies:
            kind = _attachment_type(reply)
            if kind in TOOL_BY_ATTACHMENT:
                tool = TOOL_BY_ATTACHMENT[kind]
                payload = reply.get("attachments") or {}
                break

        prose = next((r.get("body") or "" for r in replies if r.get("kind") == "bot"), "")
        case = {
            "id": f'p{trigger["id"]}',
            "day": str(trigger.get("created_at") or "")[:10],
            "actor": to_key(trigger.get("author_member_id")) or trigger.get("author") or "A1",
            "message": trigger.get("body") or "",
            # What the room had already said. `chat.py:495` gives production this
            # on every turn, so a replay without it is a harder task than the one
            # being measured.
            "history": render_history(rows, index, to_key),
            "had_images": bool(tainted),
            "reply": prose,
            # The produced attachment IS what the tool returned, so it is carried
            # through as the recorded tool result. Stubbing it broke two graders at
            # once: `posted_body_kind` could not see `type` and so judged the prose
            # of card turns whose prose the room never saw, and
            # `moneyguard.backed_amounts` had nothing to back the server-rendered
            # numbers with, flagging correct replies as inventing money.
            "result": {"ok": True, **(payload or {})},
            "expect": {},
        }
        if tool is None:
            # No card, but the bot did reply in prose — so the room read
            # `final_text`, and this is a **prose case**: no tool expectation
            # (`tool_selection` grades `None`), a real `prose_quality` grade.
            #
            # These are not leftovers. Every attachment type that yields a tool is
            # a *card* type, so a tool-gradable turn is never prose-gradable and
            # vice versa: the two populations are disjoint, and dropping these
            # would leave `prose_quality` with no coverage anywhere in the harness
            # (it is n/a on 19 of the 20 golden cases too).
            if prose.strip():
                cases.append(case)
            else:
                # No card and nothing said: nothing to grade either way.
                case["review"] = True
                cases.append(case)
            continue

        case["expect"]["tools"] = [tool]
        args = _money_args_from_attachment(tool, payload)
        if args and not tainted:
            for name in ("payer", "from", "to"):
                if name in args:
                    args[name] = to_key(args[name])
            if "participants" in args:
                args["participants"] = [to_key(p) for p in args["participants"]]
            for name in ("items", "adjustments"):
                if name in args:
                    args[name] = [dict(e, member=to_key(e.get("member"))) for e in args[name]]
            case["expect"]["args"] = {tool: args}
        cases.append(case)
    return cases


def build_baseline(cases: list[dict], *, room: int, engine: str = "cursor",
                   judge=None, keep_bodies: bool = False) -> dict:
    """A results file recording what production did, gradeable by `bench.report`.

    Each record is the turn as prod ran it: the tool it called, the money it
    passed, and the prose it posted. Graded by the same graders as a Pi run, so
    `--compare` works unchanged.

    **Stamped `baseline_kind: "recorded-prod-log"`** because this baseline is not
    a measurement of Cursor — expectation and record come from the same log row,
    so it passes by construction. It is a *reference*, and `render_compare` says so
    rather than letting a 1.0 → 0.87 read as a 13-point regression measured two
    ways.
    """
    from bench.corpus import Case
    from bench.graders import grade_prose, grade_tool_selection

    records = []
    for case in cases:
        if case.get("review"):
            continue
        tools = (case.get("expect") or {}).get("tools") or []
        tool = tools[0] if tools else None
        args = ((case.get("expect") or {}).get("args") or {}).get(tool) or {}
        record = {
            "version": 1, "case_id": case["id"], "rep": 0, "source": "prod",
            "day": case["day"], "message": case["message"],
            "had_images": case["had_images"], "room_id": None,
            # `ok: True` is injected because the *stored* attachment drops it —
            # `expense_draft` and `payment_draft` rows carry no `ok` key — while the
            # tool result the graders would have seen did have one. A prose case ran
            # no money tool, so its list is empty and `moneyguard` has only the
            # user's own message to back amounts with — which is the point: a
            # computed number in a tool-less reply is the D3 violation.
            "tools": ([{"name": tool, "args": args,
                        "result": case.get("result") or {"ok": True}}] if tool else []),
            "final_text": case.get("reply") or "", "error": None,
            "elapsed_s": 0.0, "stats": None,
        }
        # Graded here, or `--compare` sees a denominator of zero and reports every
        # case as n/a. `tool_selection` passes by construction — expectation and
        # record are the same log row — but `prose_quality` is a real measurement:
        # the judge never saw the expectation, only prod's actual reply.
        obj = Case(id=case["id"], source="prod", day=case["day"],
                   actor=str(case.get("actor") or ""), message=case["message"],
                   had_images=case["had_images"], expect=case["expect"])
        tool_verdict = grade_tool_selection(obj, record)
        prose_verdict = grade_prose(obj, record, judge=judge)
        record["grades"] = {
            "tool_selection": {"passed": tool_verdict.passed, "reason": tool_verdict.reason},
            # No prod ledger is reconstructable and prod ids mean nothing in a
            # bench room, so there is no ledger expectation to grade.
            "ledger_state": {"passed": None, "reason": "prod has no reconstructable ledger"},
            "prose_quality": {"passed": prose_verdict.passed, "reason": prose_verdict.reason},
        }
        if not keep_bodies:
            # Grading needed the bodies; the emitted file does not. Design §11.4
            # commits "the case ids, the derived expectations, and the SHA-256" —
            # not the messages. `case_rates` only reads `grades`, and the judge's
            # own `reason` is a sentence *about* the reply, not the reply.
            record.pop("message", None)
            record["final_text"] = ""
        records.append(record)
    return {"version": 1, "engine": engine, "corpus": "prod", "repeat": 1,
            "judge_model": getattr(judge, "model", None),
            "baseline_kind": "recorded-prod-log", "bodies_included": keep_bodies,
            "source_room": room, "records": records}


# --------------------------------------------------------------------------- #
# fetch + CLI
# --------------------------------------------------------------------------- #

def _get(base_url: str, path: str, key: str, timeout: float = 90.0) -> str:
    request = Request(f"{base_url.rstrip('/')}/internal/debug{path}",
                      headers={"X-Debug-Key": key})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 — operator URL
        return response.read().decode("utf-8", "replace")


def _rows_from_csv(text: str) -> list[dict]:
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        row = dict(raw)
        if row.get("id"):
            row["id"] = int(row["id"])
        if "attachments" in row:
            try:
                row["attachments"] = json.loads(row.get("attachments") or "{}")
            except json.JSONDecodeError:
                row["attachments"] = {}
        rows.append(row)
    return rows


def fetch_rows(base_url: str, room_id: int, days: int, key: str) -> list[dict]:
    """The read-only conversation CSV. `days=0` means the whole history."""
    query = f"?room_id={room_id}" + (f"&days={days}" if days else "")
    return _rows_from_csv(_get(base_url, f"/conversation.csv{query}", key))


def fetch_members(base_url: str, room_id: int, key: str) -> list[dict]:
    members = _rows_from_csv(_get(base_url, f"/tables/members.csv?room_id={room_id}", key))
    for member in members:
        try:
            member["aliases"] = json.loads(member.get("aliases") or "[]")
        except json.JSONDecodeError:
            member["aliases"] = []
    return members


def verify(path: Path, secrets: list[str]) -> list[str]:
    """Re-grep a written corpus for anything that must not be in it."""
    blob = path.read_text(encoding="utf-8")
    found = [s for s in secrets if s and s in blob]
    if "vietqr" in blob.lower():
        found.append("vietqr")
    found += [f"digit run {m}" for m in _LONG_DIGITS.findall(blob)]
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench.export_prod", description=__doc__)
    parser.add_argument("--room", type=int, required=True)
    parser.add_argument("--days", type=int, default=0, help="0 = the whole history")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=None,
                        help="also write a results file recording what prod did")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--judge-model", default=None,
                        help="grade the recorded replies' prose with this model")
    parser.add_argument("--keep-bodies", action="store_true",
                        help="leave message/reply text in the baseline file "
                             "(it is committed — off by default, see design §11.4)")
    args = parser.parse_args(argv)

    key = os.environ.get(KEY_ENV)
    if not key:
        print(f"{KEY_ENV} is not set — see deploy/DEBUGGING.md §6", file=sys.stderr)
        return 2

    members = fetch_members(args.base_url, args.room, key)
    rows = fetch_rows(args.base_url, args.room, args.days, key)
    guests = collect_person_names(rows)
    name_map, holders = build_name_map(members, guests)

    clean = sanitize(rows, name_map=name_map, holders=holders)
    key_by_member_id = {m["id"]: name_map.get(m.get("display_name"), f'A{i}')
                        for i, m in enumerate(members, start=1)}
    cases = build_cases(clean, key_by_member_id=key_by_member_id)

    pseudonyms = sorted({v for v in name_map.values() if v.startswith("A")})
    payload = {
        "source_room": args.room,
        "members": [{"key": p, "display_name": p, "nickname": p.lower()} for p in pseudonyms],
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    leaks = verify(args.out, list(name_map) + holders)
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    reviewable = sum(1 for c in cases if c.get("review"))
    print(f"wrote {args.out}: {len(cases)} cases, {reviewable} flagged review, "
          f"{len(rows)} rows, {len(members)} members, {len(guests)} guest names",
          file=sys.stderr)
    print(f"sha256 {digest}", file=sys.stderr)

    if args.baseline:
        judge = None
        if args.judge_model:
            from bench.judge import openrouter_judge
            judge = openrouter_judge(args.judge_model)
        baseline = build_baseline(cases, room=args.room, judge=judge,
                                  keep_bodies=args.keep_bodies)
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(baseline, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        graded = sum(1 for r in baseline["records"]
                     if r["grades"]["prose_quality"]["passed"] is not None)
        print(f'wrote {args.baseline}: {len(baseline["records"])} recorded turns, '
              f"{graded} with a prose verdict", file=sys.stderr)

    if leaks:
        print(f"LEAKS FOUND — do not use this file: {sorted(set(leaks))[:10]}", file=sys.stderr)
        return 1

    residual = residual_name_candidates(clean, set(name_map.values()))
    print("\nno known secret found in the output. NOW READ IT YOURSELF — the "
          "sanitizer only redacts names it was told about.", file=sys.stderr)
    if residual:
        top = list(residual.items())[:20]
        print(f"capitalized tokens left in bodies, most frequent first "
              f"({len(residual)} distinct) — skim for anything that is a person:",
              file=sys.stderr)
        print("  " + ", ".join(f"{t}×{n}" for t, n in top), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
