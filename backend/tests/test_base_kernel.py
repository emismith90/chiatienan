"""The host kernel is the framework's ``BaseKernel`` plus this host's hooks (Phase 9 review F2)."""
from app.kernel import Kernel, kernel_for
from app.tools import ToolContext
from kernos.content import Invalid
from kernos.host import BaseKernel
from kernos.plugins import EngineRun


def test_the_app_kernel_is_a_base_kernel_with_the_host_hooks(db):
    k = kernel_for(db)
    assert isinstance(k, BaseKernel) and isinstance(k, Kernel)
    assert k.default_business_id == k.seed_report["business_id"] and k.runtime == k.default_spec.runtime
    assert isinstance(k.null_tool_context(), ToolContext)
    parent = ToolContext(db=db, room_id=3, sender_member_id=1, sender_name="An", max_depth=2)
    sub = k.sub_tool_context(parent, sub={"slug": "s"}, depth=1, caps={"max_tools": 5, "max_seconds": 9})
    assert (sub.room_id, sub.sender_member_id, sub.depth, sub.max_depth, sub.caps_override) == (3, 1, 1, 2, {"max_tools": 5, "max_seconds": 9})
    assert k.eval_runner_argv("mini", 4, 9)[-6:] == ["--suite", "mini", "--version", "4", "--run-id", "9"]
    assert {p.id for p in k.packs.list()} >= {"collections", "delegation", "os_admin", "lunch_ledger"}
    assert k.registry.get("app.run.legacy", "1") and k.registry.get("kernos.persist.cards", "1")


class _NoJobs(BaseKernel):
    def null_tool_context(self):
        return ToolContext(db=None, room_id=0)


def test_a_host_without_jobs_or_a_default_business_fails_closed(db):
    k = kernel_for(db)
    bare = _NoJobs(k.store, k.data, k.adapters, runtime=k.runtime)
    bare.resolver = k.resolver
    import pytest
    with pytest.raises(Invalid, match="cannot run evals"):
        bare.start_eval_run("mini", k.seed_report["version_id"], actor="t")
    bare.resolver = None
    with pytest.raises(Invalid, match="default business"):
        bare.business_for("nowhere")
    # the framework run stage registers over any engine
    from kernos.engine import ScriptedEngine
    bare.register_engine(ScriptedEngine([]))
    assert isinstance(bare.registry.get("kernos.run.engine", "1"), EngineRun)
