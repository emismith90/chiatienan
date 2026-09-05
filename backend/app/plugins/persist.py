from kernos.kernel import BasePlugin, Stage, TurnContext
from kernos.plugins.persist import Cards as KernelCards


class Cards(BasePlugin):
    """The seeded pipeline's persist id; a delegate to ``kernos.persist.cards``
    since plan Task 3.1 (review F1)."""

    id, version, stage = "app.persist.cards", "1", Stage.persist
    config_schema = KernelCards.config_schema

    def __init__(self, inner: KernelCards) -> None:
        self._inner = inner

    async def run(self, ctx: TurnContext, config: dict) -> None:
        await self._inner.run(ctx, config)
