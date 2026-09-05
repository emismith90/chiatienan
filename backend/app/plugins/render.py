from kernos.kernel import BasePlugin, Stage, TurnContext
from kernos.plugins.render import PackRender


class LunchRender(BasePlugin):
    """The seeded pipeline's render id. Since plan Task 3.1 a one-line delegate to
    ``kernos.render.packs`` (the lunch decision lives in ``app.packs.lunch.decide``),
    so both ids run one code path; the seeded pipeline switches to the kernel id in
    Task 3.4 (review F1)."""

    id, version, stage = "app.render.lunch", "1", Stage.render
    config_schema = PackRender.config_schema

    def __init__(self, inner: PackRender) -> None:
        self._inner = inner

    async def run(self, ctx: TurnContext, config: dict) -> None:
        await self._inner.run(ctx, config)
