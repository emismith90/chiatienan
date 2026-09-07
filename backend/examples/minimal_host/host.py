"""The minimal kernos host (design §12.3; plan Task 9.3): one business, one agent, one
pack with one tool, in-memory adapters, the framework run stage over a scripted engine,
the admin API, and an AG-UI event stream — with **no chiatienan module on its path**
(``tests/test_minimal_host.py`` proves it). It is the template for the next application:
replace ``HelloPack`` with your packs, the adapters with your storage, and the scripted
engine with ``PiEngine(PiBridge(...))``.

Run it: ``uvicorn examples.minimal_host.host:app`` from ``backend/``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kernos.adapters import HostAdapters
from kernos.adapters.memory import InMemoryCards, InMemoryHistory, InMemoryMemory, InMemoryMessages
from kernos.api import AguiEventSink, admin_router
from kernos.content import (
    ContentStore, DbResolver, Models, Persona, PipelineEntry, ProfileSpec, Prompt, Runtime, ToolPackRef,
    ensure_seeded, sessions_for,
)
from kernos.content.schema import bind
from kernos.data import DataStore
from kernos.engine import ScriptedEngine
from kernos.host import BaseKernel
from kernos.kernel import Body, Principal, TurnContext, flush
from kernos.packs import BasePack, PackTool


# ------------------------------------------------------------------ the business

class HelloPack(BasePack):
    """One tool, one typed body: the business logic of this host."""

    id, version = "hello", "1"

    def tools(self, ctx: Any) -> dict[str, PackTool]:
        def say_hello(args: dict | None) -> dict:
            name = (args or {}).get("name") or "there"
            return {"ok": True, "greeting": f"Hello, {name}!"}
        return {"say_hello": PackTool("say_hello", "Greet someone by name.",
                                      {"type": "object", "properties": {"name": {"type": "string"}}}, say_hello)}

    def render(self, result) -> Body | None:
        r = result.last_result("say_hello")
        return Body(r["greeting"], {"type": "greeting"}, claimed_by_pack=True) if r else None


@dataclass
class ToolContext:
    """What the framework reads on a host's tool context during the run stage."""

    space_id: str
    agent: dict | None = None
    depth: int = 0
    max_depth: int | None = None
    turn: Any = None
    turn_id: str | None = None
    started_at: float | None = None
    calls_made: int = 0
    caps_override: dict | None = None
    sub_invocations: list = field(default_factory=list)
    tool_config: dict | None = None
    engine_spec: Any = None
    validate_call: Any = None
    validate_result: Any = None


def build_spec() -> ProfileSpec:
    e = PipelineEntry
    return ProfileSpec(
        persona=Persona(handle="hello", name="Hello"),
        prompt=Prompt(body="You are {{persona.name}}. Greet people with the say_hello tool; never guess a name."),
        models=Models(text="scripted/hello"),
        runtime=Runtime(cwd="/tmp/kernos-hello", agent_dir="/tmp/kernos-hello-agent"),
        pipeline={
            "context": [e(id="kernos.context.memory", version="1"),
                        e(id="kernos.context.history", version="1", config={"max_messages": 20, "bot_label": "hello"})],
            "prompt": [e(id="kernos.prompt.template", version="1"), e(id="kernos.prompt.sections", version="1")],
            "model": [e(id="kernos.model.passthrough", version="1")],
            "run": [e(id="kernos.run.engine", version="1")],
            "render": [e(id="kernos.render.packs", version="1")],
            "persist": [e(id="kernos.persist.cards", version="1")],
            "after": [e(id="kernos.after.trace", version="1", config={"keep_days": 7})],
        },
        tool_packs=[ToolPackRef(pack="hello")],
    )


class UtcClock:
    def now(self) -> datetime: return datetime.now(timezone.utc)
    def today(self) -> date: return self.now().date()


# --------------------------------------------------------------------- the kernel

class HelloKernel(BaseKernel):
    def __init__(self, engine_factory) -> None:
        db = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
        bind(db)
        sessions = sessions_for(db)
        store = ContentStore(sessions)
        history = InMemoryHistory()
        adapters = HostAdapters(history=history, memory=InMemoryMemory(), messages=InMemoryMessages(history),
                                clock=UtcClock(), cards=InMemoryCards(history))
        spec = build_spec()
        super().__init__(store, DataStore(sessions, audit=store.log), adapters, runtime=spec.runtime)
        self.register_packs(HelloPack())
        self.register_framework_packs()
        self.register_framework_plugins()
        self.register_engine(engine_factory)
        report = ensure_seeded(store, business_slug="hello", business_name="Hello", spec=spec,
                               agent_slug="hello", agent_name="Hello")
        self.default_business_id = report["business_id"]
        self.build_gates()
        self.resolver = DbResolver(store, default_business_slug="hello", runtime=spec.runtime, fallback=spec)

    def null_tool_context(self) -> ToolContext:
        return ToolContext(space_id="")

    def sub_tool_context(self, parent: ToolContext, *, sub: dict, depth: int, caps: dict) -> ToolContext:
        return ToolContext(space_id=parent.space_id, agent=sub, depth=depth, max_depth=parent.max_depth, caps_override=caps)


async def run_turn(kernel: HelloKernel, space_id: str, text: str, *, sink: AguiEventSink) -> TurnContext:
    spec = kernel.resolve(space_id)
    ctx = TurnContext(space_id=space_id, principal=Principal("u1", "You"), text=text, profile=spec,
                      tool_ctx=ToolContext(space_id=space_id), sink=sink, extras={"agent": kernel.agent_for(space_id)})
    await kernel.pipeline_for(spec).run(ctx)
    await flush(ctx.pending_events, sink)     # after the host's own writer lock, if it had one
    await sink.finish()
    return ctx


# ------------------------------------------------------------------------ the app

class TurnIn(BaseModel):
    text: str
    #: The scripted engine's reply lines for this turn (a real host would run PiEngine).
    script: list[dict]


def create_app() -> FastAPI:
    app = FastAPI(title="kernos minimal host")
    current: dict = {"script": []}
    kernel = HelloKernel(lambda: ScriptedEngine(current["script"]))
    app.state.kernel = kernel
    app.include_router(admin_router(lambda: kernel), prefix="/admin")

    @app.post("/spaces/{space_id}/turns")
    async def turn(space_id: str, body: TurnIn):
        current["script"] = body.script
        events: list[dict] = []

        async def write(e):
            events.append(e)
        ctx = await run_turn(kernel, space_id, body.text, sink=AguiEventSink(write, thread_id=space_id))
        return {"reply": kernel.adapters.messages.to_payload(ctx.persisted), "events": events}

    return app


app = create_app()
