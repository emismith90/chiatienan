"""Graders as plugins (design §5.5; plan Task 4.2, review F1/F10).

A grader is ``grade(case, record, world) -> Verdict``; ``passed`` is tri-state and
``None`` means *not graded* — never *passed* (a corpus of vacuous passes reads
exactly like equivalence, which is the one failure eval exists to prevent). A grader
declares ``blocking``: gate 4 refuses a publish on a blocking grader's failures and
only reports the others.

The two graders here are the business-neutral ones from chiatienan's ``bench.graders``,
with what was lunch-specific injected: ``ToolSelection`` takes the compared argument
names as config and a per-tool **equivalence** hook (lunch: "same shares under another
encoding" for ``propose_meal``); ``Prose`` takes the unbacked-amount checker, the
outcome classifier ("the room saw a card, not the prose") and the judge. Bodies are
byte-identical to the originals; ``bench.graders`` wires the same hooks and is the
oracle for that claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from kernos.eval.case import RECORD_VERSION  # noqa: F401


@dataclass
class Verdict:
    """`passed=None` means *not graded* — never *passed*."""
    passed: bool | None
    reason: str


@runtime_checkable
class Grader(Protocol):
    blocking: bool

    def grade(self, case: Any, record: dict, world: Any) -> Verdict: ...


def _last_call(record: dict, name: str) -> dict | None:
    """The last invocation of `name` in this turn, or None.

    Last rather than first: a model that corrects itself is judged on what the
    user ends up seeing, which is what `TurnResult.last_result()` returns.
    """
    for call in reversed(record.get("tools") or []):
        if call.get("name") == name:
            return call
    return None


def _recorded(call: dict, key: str):
    """What the **tool** put in the draft for `key`, if anything.

    The expectation describes the money that reached the ledger, and the tool's
    result *is* that money — so an argument the model left out is still gradeable
    whenever the tool worked it out. `amount` has one special case: a payment draft
    may carry `transfers` instead, and a single transfer is still one amount.
    """
    result = call.get("result")
    if not isinstance(result, dict):
        return None
    if result.get(key) not in (None, [], {}):
        return result[key]
    if key == "amount":
        transfers = result.get("transfers")
        if isinstance(transfers, list) and len(transfers) == 1 \
                and isinstance(transfers[0], dict):
            return transfers[0].get("amount")
    return None


def _item_key(entry, fields: tuple = ("member", "amount")) -> tuple:
    """A list entry reduced to its money — by default who and how much (`items`,
    `adjustments`); a business configures other fields (`entries`: member, buy_in,
    cash_out). Anything else on the entry is the model's prose and is not compared.
    """
    if isinstance(entry, dict):
        return tuple(entry.get(f) for f in fields)
    return (entry,)


def _ok_results(record: dict, name: str) -> list[dict]:
    """Successful result dicts for one tool, in call order (a dict whose `ok` is truthy)."""
    return [c["result"] for c in record.get("tools") or []
            if c.get("name") == name and isinstance(c.get("result"), dict)
            and c["result"].get("ok")]


def _hashable(value):
    return tuple(sorted(value.items())) if isinstance(value, dict) else value



#: What `ToolSelection` compares when the config is silent — chiatienan's money args.
DEFAULT_TOOL_SELECTION = {
    "compared_args": ["total", "payer", "participants", "from", "to", "amount", "items",
                      "guests", "adjustments"],
    "unordered": ["participants"],
    "member_amount_lists": ["items", "adjustments"],
    "count_only": ["guests"],
    "sender_defaulted": ["payer", "from"],
    "equivalence_keys": ["adjustments", "items"],
    # expectation args whose values are member **keys** (`"a1"`), resolved against the
    # world's key→id map before comparing (`bench.corpus.resolve_args` did this)
    "member_args": ["payer", "from", "to"],
    "member_list_args": ["participants"],
    # the fields that make a list entry's identity, per list (Phase 6 review F4)
    "item_fields": {"items": ["member", "amount"], "adjustments": ["member", "amount"]},
}


class ToolSelection:
    """Did the model reach for the right tool, with the right money in it?

    Superset-tolerant on the tool list — the scaffolding calls a model makes on
    its way to the answer are its own business. Strict on ``compared_args``,
    because those are the numbers people pay each other. ``equivalence`` maps a
    tool name to ``fn(args) -> hashable | None``: when both the expected and the
    actual argument sets reduce to the same value the money is the same whatever
    the encoding (lunch's ``share_map``).
    """

    blocking = True

    def __init__(self, config: dict | None = None,
                 equivalence: dict[str, Callable[[dict], Any]] | None = None) -> None:
        cfg = {**DEFAULT_TOOL_SELECTION, **(config or {})}
        self.compared_args = tuple(cfg["compared_args"])
        self.unordered = tuple(cfg["unordered"])
        self.member_amount_lists = tuple(cfg["member_amount_lists"])
        self.count_only = tuple(cfg["count_only"])
        self.sender_defaulted = tuple(cfg["sender_defaulted"])
        self.equivalence_keys = tuple(cfg["equivalence_keys"])
        self.member_args = tuple(cfg["member_args"])
        self.member_list_args = tuple(cfg["member_list_args"])
        self.item_fields = {k: tuple(v) for k, v in dict(cfg.get("item_fields") or {}).items()}
        self._equivalence = dict(equivalence or {})

    def _key(self, list_name: str):
        fields = self.item_fields.get(list_name, ("member", "amount"))
        return lambda entry: _item_key(entry, fields)

    def resolve_args(self, case, ids: dict) -> dict:
        """``expect["args"]`` with member keys turned into ids. Raises ``KeyError`` on a
        key the world does not contain, rather than grading against a wrong id."""
        args = (getattr(case, "expect", None) or {}).get("args") or {}

        def member_id(key):
            if key not in ids:
                raise KeyError(f"{case.id}: expectation names unknown member {key!r}")
            return ids[key]

        resolved = {}
        for tool_name, tool_args in args.items():
            out = dict(tool_args)
            for name in self.member_args:
                if name in out:
                    out[name] = member_id(out[name])
            for name in self.member_list_args:
                if name in out:
                    out[name] = [member_id(k) for k in out[name]]
            for name in self.member_amount_lists:
                if name in out:
                    out[name] = [dict(i, member=member_id(i["member"])) for i in out[name]]
            resolved[tool_name] = out
        return resolved

    def _args_differ(self, key: str, want, got) -> str | None:
        """Return a human reason when `got` fails to match `want`, else None."""
        if key in self.unordered and isinstance(want, list) and isinstance(got, list):
            if set(map(_hashable, want)) != set(map(_hashable, got)):
                return f"{key}: expected {sorted(map(str, want))}, got {sorted(map(str, got))}"
            return None
        if key in self.count_only and isinstance(want, list) and isinstance(got, list):
            # The count is the arithmetic; the names are the model reading prose.
            if len(want) != len(got):
                return f"guests: expected {len(want)}, got {len(got)} ({got})"
            return None
        if key in self.member_amount_lists and isinstance(want, list) and isinstance(got, list):
            item_key = self._key(key)
            if sorted(map(item_key, want), key=repr) != sorted(map(item_key, got), key=repr):
                return (f"{key}: expected {[item_key(i) for i in want]}, "
                        f"got {[item_key(i) for i in got]}")
            return None
        if want != got:
            return f"{key}: expected {want!r}, got {got!r}"
        return None



    def grade(self, case, record: dict, world=None) -> Verdict:
        """With a ``world`` (the kernel runner) the expectation's member keys are
        resolved against ``world.ids`` first; without one (``bench.run``) the caller
        already did."""
        expected = list((case.expect or {}).get("tools") or [])
        forbidden = list((case.expect or {}).get("forbidden_tools") or [])
        if not expected and not forbidden:
            return Verdict(None, "no tool expectation for this case")

        if record.get("error"):
            return Verdict(False, f"turn errored: {record['error']}")

        # A case that must NOT act (an ambiguous table the model has to ask about):
        # a forbidden tool that returned ok is a failure; none of them succeeding is a
        # graded pass — never a None (Phase 6 review F4).
        acted = [name for name in forbidden if _ok_results(record, name)]
        if acted:
            return Verdict(False, f"{acted} succeeded on a case that must ask instead")
        if not expected:
            return Verdict(True, f"asked instead of acting: none of {forbidden} succeeded")

        expect_args = ((case.expect or {}).get("args") or {})
        if world is not None and getattr(world, "ids", None):
            expect_args = self.resolve_args(case, world.ids)

        called = [c.get("name") for c in record.get("tools") or []]
        missing = [name for name in expected if name not in called]
        if missing:
            # `tools_ok` — a read-only tool a human judged to answer the same question.
            # Prod cases expect what production did, and production was the engine being
            # replaced: "@bot how much do I owe" answered with the whole group's
            # settlement is *an* answer, and `member_statement` is a better one. The
            # alternatives live in `corpus/prod_judgements.py` with a reason each, may
            # never name a money-writing tool, and are counted in the report.
            alternatives = [t for t in (case.expect or {}).get("tools_ok") or [] if t in called]
            if alternatives:
                return Verdict(True, f"called {alternatives} — a judged alternative to "
                                     f"{expected} (see corpus/prod_judgements.py)")
            return Verdict(False, f"expected {missing} to be called, got {called or 'no tools'}")

        problems = []
        for tool_name, want_args in expect_args.items():
            call = _last_call(record, tool_name)
            if call is None:
                problems.append(f"{tool_name} was never called")
                continue
            got_args = call.get("args") or {}
            for key, want in want_args.items():
                if key not in self.compared_args:
                    continue
                equivalence = self._equivalence.get(tool_name)
                if key in self.equivalence_keys and equivalence is not None:
                    # **Compare the money, not the encoding.** `p120`: the expectation's
                    # `adjustments` are production's *final shares* (they sum to the
                    # total, so `split_shares` computes a base of 0 and reproduces them),
                    # while our turn passed the bill's list prices as `items` with
                    # `discount_split="equal"` and the tool prorated them to the same six
                    # shares around a base of 54,033. Identical money, unrecognizably
                    # different arguments. Only the share map decides.
                    want_shares = equivalence({**want_args, key: want})
                    got_shares = equivalence(got_args)
                    if want_shares is not None and got_shares is not None:
                        if want_shares != got_shares:
                            problems.append(
                                f"{tool_name}: shares expected {want_shares}, got {got_shares}")
                        continue

                if key not in got_args:
                    if key in self.sender_defaulted and want == record.get("sender_member_id"):
                        # The schema permits omitting it when it is the sender, and the
                        # tool resolves it to the same id.
                        continue
                    if self._args_differ(key, want, _recorded(call, key)) is None:
                        # **The tool worked it out, which is the preferred path.**
                        # `p129` "tôi đã trả tiền A1" (expecting 27,000đ) called
                        # `propose_payment(to=A1)` with no `amount`, exactly as
                        # `record-payment` says to — the tool then reads the debt off the
                        # ledger and the model transcribes nothing (design D3). An absent
                        # argument passes only when the tool's own result matches the
                        # expectation: checked, never assumed.
                        continue
                    # Any other omitted money arg is a failure, not a comparison to skip.
                    problems.append(f"{tool_name}.{key}: expected {want!r}, absent")
                    continue
                problem = self._args_differ(key, want, got_args[key])
                if problem:
                    problems.append(f"{tool_name}.{problem}")

        if problems:
            return Verdict(False, "; ".join(problems))
        return Verdict(True, f"called {expected} with matching money args")


class _Invocation:
    """Adapter for `app.moneyguard`, which reads `.args`/`.result` via getattr.

    Handing it the runner's plain dicts would leave `backed_amounts` with only
    the user's own text, so every tool-produced amount would read as unbacked —
    a grader that fails almost everything is as useless as one that passes it.
    """

    __slots__ = ("name", "args", "result")

    def __init__(self, call: dict):
        self.name = call.get("name")
        self.args = call.get("args")
        self.result = call.get("result")


class Prose:
    """Was the reply the room actually saw a good reply?

    Two stages, cheap first: the injected ``unbacked(body, user_text, tools)`` —
    deterministic, offline (lunch: ``ledger_core.moneyguard.unbacked_amounts``) — then
    an injected LLM judge ``(case, record, rubric) -> {"ok", "reason"}``. Never
    constructed here, so this stays offline and the caller pins the model. Turns
    whose reply the host builds itself (``outcome_kind(record)`` names a card) are
    **not graded**: their prose is discarded before it reaches the room.
    """

    blocking = False

    def __init__(self, unbacked: Callable[[str, str, list], list], outcome_kind: Callable[[dict], str | None],
                 *, judge: Callable | None = None, rubric: str = "", card_labels: dict | None = None) -> None:
        self._unbacked, self._outcome_kind, self._judge = unbacked, outcome_kind, judge
        self.rubric, self.card_labels = rubric, dict(card_labels or {})

    def grade(self, case, record: dict, world=None) -> Verdict:
        if record.get("error"):
            return Verdict(False, f"turn errored: {record['error']}")

        card = self._outcome_kind(record)
        if card:
            return Verdict(None, f"not graded: the room saw {self.card_labels.get(card, card)}, "
                                 "not the model's prose")

        body = record.get("final_text") or ""
        if not body.strip():
            return Verdict(False, "empty reply")

        stray = self._unbacked(
            # The turn's history backs an amount as much as its message does: the room
            # said "tổng 324k" a message ago, the model was handed it, and repeating it
            # is not invented money. `chat.py` passes the history for the same reason.
            body, f"{case.message}\n{case.history or ''}",
            [_Invocation(c) for c in record.get("tools") or []])
        if stray:
            return Verdict(False, f"unbacked amounts in the reply: {stray}")

        if self._judge is None:
            # Not a pass. A baseline graded with no judge against a Pi run graded
            # with one is not a comparison (design §11.5).
            return Verdict(None, "not graded: no judge configured")

        answer = self._judge(case, record, self.rubric)
        if not isinstance(answer, dict) or "ok" not in answer:
            return Verdict(None, f"not graded: judge returned {answer!r}")
        reason = str(answer.get("reason") or "")
        return Verdict(bool(answer["ok"]), reason or "judge gave no reason")


class GraderRegistry:
    """``id -> factory(config, *, judge) -> Grader``. A suite names graders as
    ``{plugin, name?, config?}``; ``name`` (default: the id's last segment) is the key
    in ``record["grades"]`` and in ``spec.eval.gate``."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable] = {}

    def register(self, plugin_id: str, factory: Callable) -> None:
        self._factories[plugin_id] = factory

    def register_all(self, factories: dict[str, Callable]) -> None:
        for k, v in factories.items():
            self.register(k, v)

    def ids(self) -> list[str]:
        return sorted(self._factories)

    def build(self, ref: dict, *, judge=None) -> tuple[str, Grader]:
        plugin_id = ref["plugin"]
        try:
            factory = self._factories[plugin_id]
        except KeyError:
            raise KeyError(f"no grader {plugin_id!r} (known: {self.ids()})") from None
        name = ref.get("name") or plugin_id.rsplit(".", 1)[-1]
        return name, factory(dict(ref.get("config") or {}), judge=judge)


# --------------------------------------------------------------------------- #
# cost_latency — reported, never pass/fail
# --------------------------------------------------------------------------- #

def _percentile(sorted_values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: index `ceil(p * n) - 1`.

    No interpolation. With corpora this small an interpolated p95 would invent a
    latency no turn actually took, and the report is read as "how slow does this
    get", not as a distribution fit.
    """
    if not sorted_values:
        return None
    import math
    index = max(0, math.ceil(p * len(sorted_values)) - 1)
    return sorted_values[index]


def summarize_cost_latency(records: list[dict]) -> dict:
    """Latency, tool volume, tokens and cost across a run.

    **Reported, never pass/fail.** A slower engine that is correct is a business
    decision, not a test failure.

    `total_tokens` and `total_cost_usd` are `None` when nothing reported `stats`
    — Cursor exposes no cost, and printing `0` would claim "free" where the truth
    is "unknown", making any Pi figure look like a rise from nothing. When only
    some records carry stats the known ones are summed and `stats_n` says how
    many contributed, so a partial total can never be mistaken for a full one.
    """
    n = len(records)
    if not n:
        return {"n": 0, "error_n": 0, "p50_s": None, "p95_s": None,
                "mean_tool_calls": None, "total_tokens": None,
                "total_cost_usd": None, "stats_n": 0}

    # An errored turn's elapsed time is a latency fact, not a gap in the data.
    elapsed = sorted(float(r.get("elapsed_s") or 0.0) for r in records)
    stats = [r["stats"] for r in records if isinstance(r.get("stats"), dict)]

    return {
        "n": n,
        "error_n": sum(1 for r in records if r.get("error")),
        "p50_s": _percentile(elapsed, 0.50),
        "p95_s": _percentile(elapsed, 0.95),
        "mean_tool_calls": sum(len(r.get("tools") or []) for r in records) / n,
        "total_tokens": sum(int(s.get("tokens") or 0) for s in stats) if stats else None,
        "total_cost_usd": sum(float(s.get("cost") or 0.0) for s in stats) if stats else None,
        "stats_n": len(stats),
    }
