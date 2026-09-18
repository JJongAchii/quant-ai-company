import asyncio
import contextlib
from urllib.parse import urlsplit

from temporalio import activity

from ..contracts import ProviderFault, ProviderRequest, ProviderResponse
from ..execution import provider_for
from .contracts import NewsSource
from .feeds import fetch_feed
from .originals import fetch_original
from .store import NewsStore


class NewsCollector:
    def __init__(self, company, feed_fetcher=fetch_feed, article_fetcher=fetch_original):
        self.store = NewsStore(company)
        self.feed_fetcher = feed_fetcher
        self.article_fetcher = article_fetcher

    async def tick(self):
        if not self.store.company.settings.company_news_enabled:
            return {"state": "paused"}
        source = await asyncio.to_thread(self.store.claim_source)
        result = {"state": "idle"}
        if source:
            receipt = await asyncio.to_thread(self.feed_fetcher, NewsSource.model_validate(source["config"]),
                                              source["etag"], source["modified"])
            result = await asyncio.to_thread(self.store.save_feed, source, receipt)
        article = await asyncio.to_thread(self.store.claim_article)
        if article:
            # Revalidate URLs against the frozen source registration before fetching original content.
            if urlsplit(article["url"]).hostname not in article["config"]["article_hosts"]:
                receipt = {"ok": False, "error": "article_host_changed"}
            else:
                receipt, _ = await asyncio.to_thread(self.article_fetcher, article["url"])
            await asyncio.to_thread(self.store.save_original, article, receipt)
            result["article_fetched"] = True
        return result

    @activity.defn(name="company_news_collect")
    async def activity_tick(self):
        return await self.tick()


class NewsEditor:
    def __init__(self, company, provider=None):
        self.store = NewsStore(company)
        self.provider = provider if provider is not None else provider_for(company)

    async def tick(self, heartbeat=False):
        ready = await asyncio.to_thread(self.store.prepare_review)
        if ready["state"] != "ready":
            return ready
        request = ProviderRequest.model_validate(ready["request"])
        task = asyncio.create_task(self.provider.run(request))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=5)
                if heartbeat:
                    activity.heartbeat({"review_id": request.request_id})
            response = ProviderResponse.model_validate(await task)
            if response.request_id != request.request_id:
                raise ValueError("news_response_identity_mismatch")
            return await asyncio.to_thread(self.store.commit_review, response)
        except ProviderFault as exc:
            await asyncio.to_thread(self.store.fault, request.request_id, exc.code, exc.retry_after_seconds)
            return {"state": "defer" if exc.code in {"quota", "busy", "unavailable"} else "blocked", "reason": exc.code}
        except ValueError:
            await asyncio.to_thread(self.store.fault, request.request_id, "invalid_news_proposal")
            return {"state": "blocked", "reason": "invalid_news_proposal"}
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    @activity.defn(name="company_news_review")
    async def activity_tick(self):
        return await self.tick(heartbeat=True)
