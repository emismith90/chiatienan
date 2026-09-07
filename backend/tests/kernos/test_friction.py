"""The deterministic friction detectors (plan Phase 10.1)."""
from kernos.friction import DETECTORS, SLOW_MS, detect, report


def _s(**kw):
    """A trace row as ``TraceStore.list`` returns it (summary only)."""
    summary = {"verdicts": [], "capped": False, "error": None, "elapsed_ms": 1000, "tools": []}
    summary.update(kw.pop("summary", {}))
    return {"turn_id": kw.pop("turn_id", "t"), "started": kw.pop("started", "2026-09-06T10:00:00+00:00"),
            "summary": summary}


def _verdict(plugin, outcome, reason=None):
    return {"plugin": plugin, "outcome": outcome, "reason": reason}


def test_each_detector_fires_on_its_own_shape_and_nothing_else():
    rows = [
        _s(turn_id="forge", summary={"verdicts": [_verdict("app.validate.fabricated_commit", "block", "fabricated commit")]}),
        _s(turn_id="broke", summary={"error": "BridgeError: sidecar died"}),
        _s(turn_id="rule", summary={"verdicts": [_verdict("kernos.validate.sum_equals", "block", "total-is-items: delta +100,000")]}),
        _s(turn_id="money", summary={"verdicts": [_verdict("app.validate.unbacked_amounts", "warn", "unbacked amounts [999000]")]}),
        _s(turn_id="cut", summary={"capped": True, "elapsed_ms": 120_600}),
        _s(turn_id="slow", summary={"elapsed_ms": SLOW_MS + 1}),
        _s(turn_id="fine"),
    ]
    out = report(rows)
    assert out["scanned"] == 7 and out["clean"] is False
    by_id = {f["id"]: f for f in out["findings"]}
    assert set(by_id) == {"fabricated_commit", "run_error", "rule_blocked", "unbacked_amounts", "capped", "slow"}
    assert all(f["count"] == 1 for f in out["findings"])
    assert by_id["fabricated_commit"]["examples"] == [{"turn_id": "forge", "reason": "fabricated commit"}]
    assert by_id["run_error"]["examples"][0]["reason"] == "BridgeError: sidecar died"
    assert "sum_equals" in by_id["rule_blocked"]["examples"][0]["reason"]
    assert by_id["unbacked_amounts"]["examples"][0]["reason"] == "unbacked amounts [999000]"
    assert by_id["capped"]["examples"][0]["reason"] == "cut short after 120.6s"
    assert by_id["slow"]["examples"][0]["reason"] == "60.0s"
    # the forgery guard is not double-counted as a generic rule block
    assert by_id["rule_blocked"]["count"] == 1 and by_id["rule_blocked"]["examples"][0]["turn_id"] == "rule"
    # a capped turn is not also reported as slow
    assert by_id["capped"]["count"] == 1 and by_id["slow"]["examples"][0]["turn_id"] == "slow"


def test_findings_are_ordered_worst_first_and_carry_counts_and_shares():
    rows = ([_s(turn_id=f"s{i}", summary={"elapsed_ms": SLOW_MS + 1}) for i in range(6)]
            + [_s(turn_id="forge", summary={"verdicts": [_verdict("app.validate.fabricated_commit", "block", "r")]})]
            + [_s(turn_id=f"w{i}", summary={"verdicts": [_verdict("app.validate.unbacked_amounts", "warn", "r")]}) for i in range(3)])
    out = report(rows)
    assert [f["id"] for f in out["findings"]] == ["fabricated_commit", "unbacked_amounts", "slow"]
    slow = out["findings"][-1]
    assert slow["count"] == 6 and slow["share"] == 0.6 and len(slow["examples"]) == 3      # examples are capped at 3
    assert out["findings"][0]["share"] == 0.1
    assert out["from"] and out["to"]


def test_a_quiet_space_is_clean_and_says_so():
    out = report([_s(turn_id=str(i)) for i in range(5)])
    assert out == {"scanned": 5, "from": "2026-09-06T10:00:00+00:00", "to": "2026-09-06T10:00:00+00:00",
                   "findings": [], "clean": True}
    assert report([])["clean"] is True and report([])["scanned"] == 0
    # a turn with no summary at all does not crash a detector
    assert report([{"turn_id": "x", "started": None, "summary": None}])["clean"] is True


def test_every_detector_documents_itself():
    ids = [d.id for d in DETECTORS]
    assert len(ids) == len(set(ids))
    for d in DETECTORS:
        assert d.severity in ("high", "medium", "low")
        assert len(d.what) > 40 and len(d.suggests) > 40 and d.what.strip().endswith(".")
    assert detect([]) == []
