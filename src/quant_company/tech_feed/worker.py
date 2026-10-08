"""Dedicated zero-model collector; does not restart the shared research worker."""

import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from ..company import Company
from ..config import Settings
from .runner import TechFeedCollector
from .workflow import TechFeedCollectionWorkflow


def make_worker(client, company, collector=None):
    collector = collector or TechFeedCollector(company)
    return Worker(client, task_queue=company.settings.temporal_task_queue + "-tech-feed-dedicated",
                  workflows=[TechFeedCollectionWorkflow], activities=[collector.activity_tick],
                  max_concurrent_activities=1, max_cached_workflows=5,
                  graceful_shutdown_timeout=timedelta(seconds=10))


async def main():
    settings = Settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                  api_key=settings.temporal_api_key.get_secret_value() or None,
                                  tls=settings.temporal_tls)
    async with make_worker(client, Company(settings)):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
