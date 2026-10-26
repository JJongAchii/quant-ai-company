"""Dedicated zero-model collector; does not restart the shared research worker."""

import asyncio

from ..runtime import serve, tech_feed_loop


def loops(company):
    # Without TECH_FEED_DEDICATED_WORKER the shared worker runs the collection (runtime.worker_loops).
    settings = company.settings
    return [tech_feed_loop(company)] if settings.tech_feed_enabled and settings.tech_feed_dedicated_worker else []


async def main():
    await serve(loops)


if __name__ == "__main__":
    asyncio.run(main())
