"""The eval tables through `ContentStore` (plan Task 4.2)."""
import pytest

from kernos.content import ContentStore, NotFound
from kernos.content.errors import Conflict
from kernos.content.errors import Invalid
from kernos.eval import EvalCase


def _store(db):
    store = ContentStore(db.session)
    b = store.create_business("acme", "Acme")
    return store, b["id"]


def test_cases_upsert_by_slug_and_list_without_bodies(db):
    store, bid = _store(db)
    case = EvalCase(id="G1", source="meals", day="2026-07-20", actor="m1", message="x").to_dict()
    a = store.put_case(bid, "G1", case, actor="admin", source="imported", tags=["golden"])
    b = store.put_case(bid, "G1", {**case, "message": "y"}, actor="admin", source="imported", tags=["golden"])
    assert a["id"] == b["id"] and store.get_case(bid, "G1")["case"]["message"] == "y"
    store.put_case(bid, "cap-1", {**case, "id": "cap-1"}, actor="kernos", source="captured", review=True)
    listed = store.list_cases(bid)
    assert [c["slug"] for c in listed] == ["G1", "cap-1"] and "case" not in listed[0]
    assert [c["slug"] for c in store.list_cases(bid, review=False)] == ["G1"]
    assert [c["slug"] for c in store.list_cases(bid, source="captured")] == ["cap-1"]
    assert store.list_cases(bid, full=True)[0]["case"]["id"] == "G1"
    store.delete_case(bid, "cap-1", actor="admin")
    with pytest.raises(NotFound):
        store.get_case(bid, "cap-1")
    assert [a["action"] for a in store.audit(limit=10)][:2] == ["delete", "put"]


def test_suites_rubrics_and_runs(db):
    store, bid = _store(db)
    with pytest.raises(Invalid):
        store.put_suite(bid, "s", actor="admin", case_slugs=[], graders=["bad"])
    suite = store.put_suite(bid, "lunch-typical", actor="admin", case_slugs=["G1"],
                            graders=[{"plugin": "lunch_ledger.eval.tool_selection"}], judge={"rubric": "prose"}, repeat=0)
    assert suite["repeat"] == 1 and store.get_suite(bid, "lunch-typical")["case_slugs"] == ["G1"]
    assert [s["slug"] for s in store.list_suites(bid)] == ["lunch-typical"]
    store.put_rubric(bid, "prose", "Be terse.", actor="admin")
    assert store.get_rubric(bid, "prose")["body"] == "Be terse." and len(store.list_rubrics(bid)) == 1
    run = store.create_run(suite["id"], profile_version_id=3, spec_sha="abc", actor="admin", judge_model=None)
    assert run["status"] == "running" and run["finished"] is None
    with pytest.raises(Invalid):
        store.finish_run(run["id"], status="meh")
    done = store.finish_run(run["id"], status="done", records=[{"case_id": "G1"}], summary={"graders": []})
    assert done["status"] == "done" and done["records"] == [{"case_id": "G1"}] and done["finished"]
    assert store.get_run(run["id"])["summary"] == {"graders": []}
    listed = store.list_runs(profile_version_id=3)
    assert len(listed) == 1 and "records" not in listed[0] and store.list_runs(suite_id=999) == []
    with pytest.raises(NotFound):
        store.create_run(999, profile_version_id=3, spec_sha="abc", actor="admin")
    with pytest.raises(Conflict):                       # runs are gate evidence
        store.delete_suite(bid, "lunch-typical", actor="admin")
    empty = store.put_suite(bid, "empty", actor="admin", case_slugs=[], graders=[])
    store.delete_suite(bid, "empty", actor="admin")
    with pytest.raises(NotFound):
        store.get_suite(bid, "empty")
    assert empty["slug"] == "empty"
