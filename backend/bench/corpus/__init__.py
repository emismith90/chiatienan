"""Corpus loaders over the datasets the repo already trusts.

`tests/golden/meals.py` and `tests/golden/scenario_week.py` stay the single
source of truth for payloads and expectations — this package imports them rather
than restating them, so a change to a golden expectation reaches the benchmark
without anyone remembering to mirror it.

What it adds on top is everything the goldens deliberately lack, because they
were written for deterministic replay rather than LLM replay:

* **A message per meal case.** `meals.py` has none; see `meal_messages.py`.
* **The prior steps of each week case.** A week step's expectation assumes the
  world every earlier step built, and 10 of the 21 steps carry no `message` at
  all — two `confirm_pending` button presses and the eight `s11*` payments that
  zero the ledger, which is exactly what `s12`'s `empty: True` rests on. Each
  case therefore carries **every** earlier step in list order, message-less ones
  included, for `bench.world` to dispatch.
* **The room's member specs**, bank details and all. `scenario_week.MEMBERS`
  gives a1/a2/a4 banks "so QR builds succeed"; seed the room without them and
  `make_qr_url` raises `QRError` for every payee, so `qr_payees` could never
  pass — on either engine, which would read as equivalence.

### Member references in `expect`

Everything in `expect` that names a person uses the **corpus key** (`"a1"`,
`"m3"`), never a database id — ids are assigned per run. `bench.world` returns
the key→id map and the runner resolves expectations against it just before
grading. Amounts are integer VND throughout.
"""
from __future__ import annotations

import base64
import copy
import json
import mimetypes
from dataclasses import dataclass, field, replace
from pathlib import Path

from bench.corpus import meal_messages

_HERE = Path(__file__).resolve().parent

#: Sanitized production conversations (Task 7). Gitignored by default — nothing
#: downstream needs the bodies in git, only on disk when `--corpus prod` runs.
PROD_PATH = _HERE / "prod_conversations.json"

#: Synthetic bill-image cases (Task 7 Step 7). The golden corpora carry no
#: images and prod images are stripped, so without these the vision path has
#: zero benchmark coverage.
BILLS_PATH = _HERE / "bills" / "cases.json"

#: The golden meal cases have no date of their own. Monday of the week the
#: scenario corpus uses, so `this_week` / `since_last` behave predictably.
MEALS_DAY = "2026-07-20"

#: `meals.py` addresses members by 1-based index and its docstring names them
#: An/Bình/Cường/Dung. Spelling those out gives `find_members` something real to
#: resolve; the golden arithmetic is by index, so the names cannot affect it.
#: No bank details — `tests/test_golden_meals.py` seeds none either, and no meal
#: case settles.
MEALS_MEMBERS = [
    {"key": "m1", "display_name": "An", "nickname": "an"},
    {"key": "m2", "display_name": "Bình", "nickname": "binh"},
    {"key": "m3", "display_name": "Cường", "nickname": "cuong"},
    {"key": "m4", "display_name": "Dung", "nickname": "dung"},
]

#: Which tool a week step's `kind` implies. Same mapping
#: `tests/test_scenario_week_llm.py` has used since the LLM eval was added.
EXPECTED_TOOL = {
    "meal_confirmed": "propose_meal",
    "leave_pending": "propose_meal",
    "payment": "propose_payment",
    "add_member": "add_member",
    "settle": "settle_period",
}


@dataclass
class Case:
    """One benchmark case: a world to rebuild, then a single message to replay."""

    id: str
    source: str
    #: ISO date; the runner freezes the clock to noon ICT on it.
    day: str
    #: Corpus key of whoever sends `message`.
    actor: str
    #: Member specs to seed the room with, before `prior_steps` run.
    members: list[dict] = field(default_factory=list)
    #: Every earlier step, in order, message-less ones included.
    prior_steps: list[dict] = field(default_factory=list)
    message: str = ""
    #: `[{"data": <base64>, "mimeType": …}]`, as `run_turn` takes them.
    images: list[dict] = field(default_factory=list)
    #: True when the real turn saw a photo, even one this corpus cannot carry.
    had_images: bool = False
    expect: dict = field(default_factory=dict)


def _money_args(spec: dict, kind: str) -> dict:
    """The money-bearing arguments the model must get right, in corpus keys."""
    if kind in ("meal_confirmed", "leave_pending"):
        args = {"total": spec["total"], "payer": spec["payer"],
                "participants": list(spec["participants"])}
        # `guests` and `adjustments` are money-bearing too, though the plan's
        # MONEY_ARGS list omits both: a lost guest divides by too few heads, and a
        # lost adjustment splits evenly what one person ordered extra of.
        if spec.get("guests"):
            args["guests"] = list(spec["guests"])
        if spec.get("adjustments"):
            args["adjustments"] = [dict(a) for a in spec["adjustments"]]
        return {"propose_meal": args}
    if kind == "payment":
        return {"propose_payment": {"from": spec["from"], "to": spec["to"],
                                    "amount": spec["amount"]}}
    return {}


def _meal_case(case: dict) -> Case:
    try:
        message = meal_messages.MESSAGES[case["id"]]
    except KeyError:
        raise KeyError(
            f"golden meal case {case['id']} has no message in "
            "bench/corpus/meal_messages.py — add one so the case can be replayed"
        ) from None

    key = lambda idx: f"m{idx}"  # noqa: E731 — 1-based index → corpus key
    spec = {"total": case["total"], "payer": key(case["payer"]),
            "participants": [key(p) for p in case["participants"]],
            "guests": case.get("guests") or [],
            "adjustments": [{"member": key(a["member"]), "amount": a["amount"]}
                            for a in case.get("adjustments") or []]}
    expect = {
        "tools": ["propose_meal"],
        "args": _money_args(spec, "meal_confirmed"),
        # Post-commit facts from the golden case. A benchmark turn only drafts,
        # so these grade the draft's arithmetic rather than the ledger's.
        "shares": {key(k): v for k, v in case["shares"].items()},
        "balances": {key(k): v for k, v in case["balances"].items()},
        "tracked": case["tracked"],
    }
    if case.get("expect_meta"):
        expect["meta"] = dict(case["expect_meta"])
    return Case(id=case["id"], source="meals", day=MEALS_DAY,
                actor=key(case["payer"]), members=copy.deepcopy(MEALS_MEMBERS),
                prior_steps=[], message=message, expect=expect)


def _week_case(step: dict, prior: list[dict], members: list[dict]) -> Case:
    expect = copy.deepcopy(step.get("expect") or {})
    expect["tools"] = [EXPECTED_TOOL[step["kind"]]]
    args = _money_args(step, step["kind"])
    if args:
        expect["args"] = args
    if step["kind"] == "add_member":
        expect["new_member"] = step["new_member"]
    return Case(id=step["id"], source="week", day=step["day"], actor=step["actor"],
                members=copy.deepcopy(members), prior_steps=copy.deepcopy(prior),
                message=step["message"], expect=expect)


def _load_meals() -> list[Case]:
    from tests.golden.meals import CASES
    return [_meal_case(c) for c in CASES]


def _load_week() -> list[Case]:
    from tests.golden.scenario_week import MEMBERS, STEPS
    cases = []
    for i, step in enumerate(STEPS):
        # A step with no `message` is not an LLM turn: `confirm_pending` is a UI
        # button press, and the `s11*` payments were never typed at all. They
        # still matter — as prior steps for everything after them.
        if not step.get("message"):
            continue
        cases.append(_week_case(step, STEPS[:i], MEMBERS))
    return cases


def _case_from_json(raw: dict, source: str, members: list[dict],
                    image_dir: Path | None = None) -> Case:
    images = []
    for name in raw.get("images") or []:
        path = (image_dir or _HERE) / name
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        images.append({"data": base64.b64encode(path.read_bytes()).decode(),
                       "mimeType": mime})
    return Case(id=raw["id"], source=source, day=raw["day"], actor=raw["actor"],
                members=copy.deepcopy(raw.get("members") or members),
                prior_steps=copy.deepcopy(raw.get("prior_steps") or []),
                message=raw.get("message") or "", images=images,
                had_images=bool(raw.get("had_images") or images),
                expect=copy.deepcopy(raw.get("expect") or {}))


def _load_json_corpus(path: Path, source: str) -> list[Case]:
    """Load a file-backed corpus, tolerating its absence.

    Absence is the normal state: the prod corpus is gitignored, and a checkout
    without it must still be able to run `--corpus meals`.
    """
    if not path.exists():
        return []
    blob = json.loads(path.read_text(encoding="utf-8"))
    members = blob.get("members") or []
    return [_case_from_json(raw, source, members, image_dir=path.parent)
            for raw in blob.get("cases") or []
            # A case whose expectation could not be derived waits for a human.
            if not raw.get("review")]


#: How many prod representatives to keep per behaviour cluster in `typical`.
TYPICAL_PER_CLUSTER = 2


def _load_typical() -> list[Case]:
    """Every golden case, plus a few real ones per behaviour cluster.

    `--corpus all` is 130 cases and ~45 minutes of real model time, which is too
    slow to iterate against. `typical` keeps the whole of the golden corpora — they
    are the only cases with *exact* money expectations, so they are the signal worth
    having every time — and generalizes the 107 prod cases down to representatives.

    The prod corpus clusters cleanly by the tool its turn produced:

        (prose only) 26 · settle_period 24 · propose_payment 21 · propose_meal 12
        member_statement 11 · pick_random 10 · get_period_summary 3

    so `typical` takes `TYPICAL_PER_CLUSTER` from each, **preferring cases that carry
    money args** (a graded `total` beats a graded tool name) and taking one
    image-tainted case if the cluster has one, since that path is otherwise covered
    only by the synthetic bills.
    """
    prod = _load_json_corpus(PROD_PATH, "prod")
    clusters: dict[str, list[Case]] = {}
    for case in prod:
        key = (case.expect.get("tools") or ["prose"])[0]
        clusters.setdefault(key, []).append(case)

    picked: list[Case] = []
    for key in sorted(clusters):
        members = clusters[key]
        # Money args first, then image-tainted, then whatever is left — a stable
        # order so the corpus is the same set every run.
        members.sort(key=lambda c: (not c.expect.get("args"), not c.had_images, c.id))
        picked.extend(members[:TYPICAL_PER_CLUSTER])

    return _load_meals() + _load_week() + load("bills") + picked


def load(name: str) -> list[Case]:
    """Load a corpus by name: `meals`, `week`, `prod`, `bills`, `typical`, or `all`."""
    if name == "typical":
        return _load_typical()
    if name == "meals":
        return _load_meals()
    if name == "week":
        return _load_week()
    if name == "prod":
        return _load_json_corpus(PROD_PATH, "prod")
    if name == "bills":
        return _load_json_corpus(BILLS_PATH, "bills")
    if name == "all":
        return _load_meals() + _load_week() + load("prod") + load("bills")
    raise ValueError(f"unknown corpus: {name!r} "
                     "(expected meals, week, prod, bills, or all)")


#: Money args whose value names a **member** rather than an amount. Everything
#: else in `expect["args"]` is an integer VND or a free string and is left alone.
_MEMBER_ARGS = ("payer", "from", "to")
_MEMBER_LIST_ARGS = ("participants",)


def resolve_args(case: Case, ids: dict[str, int]) -> Case:
    """Return a copy of `case` with `expect["args"]` member keys turned into ids.

    `grade_tool_selection` takes no key→id map — it compares the model's raw
    arguments, which are database ids — so the runner resolves first.
    `grade_ledger_state` is the other convention and resolves internally, which is
    what lets `compare_settlement` be the same code `test_scenario_week` imports.

    Raises `KeyError` on a key the world does not contain, rather than grading
    against a silently wrong id.
    """
    args = (case.expect or {}).get("args")
    if not args:
        return case

    def member_id(key):
        if key not in ids:
            raise KeyError(f'{case.id}: expectation names unknown member {key!r}')
        return ids[key]

    resolved = {}
    for tool_name, tool_args in args.items():
        out = dict(tool_args)
        for name in _MEMBER_ARGS:
            if name in out:
                out[name] = member_id(out[name])
        for name in _MEMBER_LIST_ARGS:
            if name in out:
                out[name] = [member_id(k) for k in out[name]]
        for name in ("items", "adjustments"):
            if name in out:
                out[name] = [dict(i, member=member_id(i["member"])) for i in out[name]]
        resolved[tool_name] = out

    expect = dict(case.expect)
    expect["args"] = resolved
    return replace(case, expect=expect)
