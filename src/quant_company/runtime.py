import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from .company import Company
from .config import Settings
from .execution import TurnExecutor
from .news.runner import NewsCollector, NewsEditor
from .news.workflow import NewsCollectionWorkflow, NewsEditorialWorkflow
from .research.runner import ResearchRunner
from .research.workflow import ResearchWorkflow
from .slack import SlackIngress, SlackOutbox
from .staff.runner import StaffRunner
from .staff.workflow import StaffDevelopmentWorkflow
from .workflow import CompanyTurnWorkflow


async def connect(settings: Settings):
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                api_key=settings.temporal_api_key.get_secret_value() or None,
                                tls=settings.temporal_tls)


def make_worker(client, company, executor=None):
    executor = executor or TurnExecutor(company)
    staff = StaffRunner(company, executor.provider)
    news = NewsEditor(company, executor.provider)
    return Worker(client, task_queue=company.settings.temporal_task_queue,
                  workflows=[CompanyTurnWorkflow, StaffDevelopmentWorkflow, NewsEditorialWorkflow],
                  activities=[executor.activity_execute, executor.activity_block, staff.activity_tick, news.activity_tick],
                  max_concurrent_activities=1, max_cached_workflows=100,
                  graceful_shutdown_timeout=timedelta(seconds=10))


def make_news_collector(client, company, collector=None):
    collector = collector or NewsCollector(company)
    return Worker(client, task_queue=company.settings.temporal_task_queue + "-news-collection",
                  workflows=[NewsCollectionWorkflow], activities=[collector.activity_tick],
                  max_concurrent_activities=1, max_cached_workflows=10,
                  graceful_shutdown_timeout=timedelta(seconds=10))


def make_research_worker(client, company, runner=None):
    runner = runner or ResearchRunner(company)
    return Worker(client, task_queue=company.settings.temporal_task_queue + "-research",
                  workflows=[ResearchWorkflow], activities=[runner.activity_tick],
                  max_concurrent_activities=1, max_cached_workflows=10,
                  graceful_shutdown_timeout=timedelta(seconds=10))


async def dispatch_once(client, company):
    if company.settings.company_research_enabled and not getattr(company, "_research_workflow_started", False):
        try:
            await client.start_workflow(ResearchWorkflow.run, id="company-research-reconciliation-v1",
                                        task_queue=company.settings.temporal_task_queue + "-research",
                                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        except WorkflowAlreadyStartedError:
            pass
        company._research_workflow_started = True
    if company.settings.company_news_enabled and not getattr(company, "_news_workflows_started", False):
        for workflow, identity, queue in (
            (NewsCollectionWorkflow.run, "company-news-collection-v1", company.settings.temporal_task_queue + "-news-collection"),
            (NewsEditorialWorkflow.run, "company-news-editorial-v1", company.settings.temporal_task_queue),
        ):
            try:
                await client.start_workflow(workflow, id=identity, task_queue=queue,
                                            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
            except WorkflowAlreadyStartedError:
                pass
        company._news_workflows_started = True
    if company.settings.company_staff_development_enabled and not getattr(company, "_staff_workflow_started", False):
        try:
            await client.start_workflow(StaffDevelopmentWorkflow.run, id="company-staff-development-v1",
                                        task_queue=company.settings.temporal_task_queue,
                                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        except WorkflowAlreadyStartedError:
            pass
        company._staff_workflow_started = True
    turns = await asyncio.to_thread(company.pending_starts)
    for turn in turns:
        try:
            await client.start_workflow(CompanyTurnWorkflow.run, turn["id"], id="company-turn-" + turn["id"],
                                        task_queue=company.settings.temporal_task_queue,
                                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        except WorkflowAlreadyStartedError:
            pass
        await asyncio.to_thread(company.mark_started, turn["id"])
    return len(turns)


async def worker_main(settings=None):
    settings = settings or Settings()
    company = Company(settings)
    client = await connect(settings)
    async with make_worker(client, company), make_news_collector(client, company), make_research_worker(client, company):
        await asyncio.Event().wait()


async def dispatch_main(settings=None):
    settings = settings or Settings()
    company = Company(settings)
    client = await connect(settings)
    outbox = SlackOutbox(company, SlackIngress(settings, company).credentials)
    while True:
        await dispatch_once(client, company)
        await outbox.send_one()
        await asyncio.sleep(1.1)
