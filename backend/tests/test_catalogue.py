"""The component catalogue (plan Phase 13.1): the scanner's output an operator assembles
a profile from.

The two questions it has to get right are about the packs whose tools are *not* a static
list: `os_admin` (which tools depends on the agent's capability verbs) and `delegation` /
`collections` (whose tool names come out of the database). Reporting those as "no tools"
would tell an operator their agent is emptier than it is.
"""
import pytest

from app.kernel import kernel_for
from kernos.content.gates import MONEY_TOOLS
from kernos.engine.base import BUILTIN_TOOL_NAMES
from kernos.packs import BasePack, PackTool


@pytest.fixture
def kernel(db):
    return kernel_for(db)


def _by_id(cat) -> dict:
    return {p["id"]: p for p in cat["packs"]}


def test_every_pack_reports_its_tools_with_a_schema(kernel):
    cat = kernel.catalogue()
    packs = _by_id(cat)
    assert {"lunch_ledger", "ledger_tools", "room_members", "lunch_places",
            "os_admin", "delegation", "collections"} <= set(packs)
    lunch = packs["lunch_ledger"]
    assert lunch["handles_money"] is True and lunch["error"] is None
    assert lunch["draft_kinds"] and lunch["tools"]
    for tool in lunch["tools"]:
        assert tool["description"] and tool["schema"].get("type") == "object"
    propose = next(t for t in lunch["tools"] if t["name"] == "propose_meal")
    assert propose["money"] is True and propose["commit"] is True


def test_a_read_only_pack_is_not_marked_money(kernel):
    places = _by_id(kernel.catalogue())["lunch_places"]
    assert places["handles_money"] is False
    assert all(t["money"] is False for t in places["tools"])


def test_os_admin_describes_its_whole_set_although_it_is_dynamic(kernel):
    """`tools(ctx)` returns nothing without an agent, so the catalogue asks the pack —
    which grants itself every verb — rather than reading `all_tool_names` and losing the
    descriptions and schemas."""
    os_admin = _by_id(kernel.catalogue())["os_admin"]
    assert os_admin["dynamic"] is True and os_admin["framework_managed"] is False
    names = {t["name"] for t in os_admin["tools"]}
    assert {"cms_get_profile", "cms_draft_change", "cms_publish", "cms_run_eval"} <= names
    assert names == set(os_admin["tool_names"])
    for tool in os_admin["tools"]:
        assert tool["description"] and tool["schema"]["type"] == "object"
    # nothing in the CMS pack may back a number
    assert os_admin["evidence"] is False


def test_delegation_is_framework_managed_and_collections_is_per_space(kernel):
    """A profile must not list `delegation`: `prepare_tool_context` adds it whenever the
    agent delegates, and two sources of one tool name make `compose_tools` raise."""
    packs = _by_id(kernel.catalogue())
    assert packs["delegation"]["framework_managed"] is True
    assert packs["delegation"]["dynamic"] is True and packs["delegation"]["tools"] == []
    assert packs["collections"]["dynamic"] is True and packs["collections"]["framework_managed"] is False


def test_a_pack_that_raises_degrades_instead_of_taking_the_screen_down(kernel):
    class Broken(BasePack):
        id, version = "broken", "1"
        def tools(self, ctx):
            raise RuntimeError("needs a real turn")

    class Fine(BasePack):
        id, version = "fine", "1"
        def tools(self, ctx):
            return {"ping": PackTool("ping", "d", {"type": "object", "properties": {}}, lambda a: {})}

    kernel.register_packs(Broken(), Fine())
    packs = _by_id(kernel.catalogue())
    assert packs["broken"]["tools"] == [] and "needs a real turn" in packs["broken"]["error"]
    assert [t["name"] for t in packs["fine"]["tools"]] == ["ping"]
    assert packs["lunch_ledger"]["tools"], "one bad pack must not cost the others"


def test_the_builtin_and_risky_tool_lists_come_from_one_place_each(kernel):
    cat = kernel.catalogue()
    assert cat["builtin_tools"] == list(BUILTIN_TOOL_NAMES)
    # the gates' own set, not a second copy of it
    assert cat["risky_builtin_tools"] == sorted(MONEY_TOOLS)
    assert set(cat["risky_builtin_tools"]) <= set(cat["builtin_tools"])
    assert cat["source_kinds"] == ["prompt", "rule", "skill", "template"]
