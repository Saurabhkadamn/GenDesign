"""One-time deployment command for LangGraph checkpoint tables."""
import asyncio

from .services.checkpoints import setup_and_harden


async def setup() -> None:
    await setup_and_harden()


if __name__ == "__main__":
    asyncio.run(setup())
