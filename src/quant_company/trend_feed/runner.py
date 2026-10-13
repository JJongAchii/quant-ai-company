import asyncio
from datetime import timedelta

from temporalio import activity

from ..company import as_json, fingerprint
from ..contracts import ProviderFault, ProviderRequest, ProviderResponse
from ..execution import provider_for
from ..news.feeds import canonical_url, clean, timestamp
from . import schedule
from .sources import NaverClient, article_source, fetch_google, read_article, trend_payload, trend_summary
from .store import TrendFeedStore


class TrendFeedCollector:
    def __init__(self, company, fetcher=fetch_google, naver=None, reader=read_article):
        self.store = TrendFeedStore(company)
        self.fetcher, self.reader = fetcher, reader
        self.naver = naver or NaverClient(company.settings)

    async def tick(self):
        if not self.store.authorized():
            return {"state": "paused"}
        result = {"state": "idle"}
        claimed = await asyncio.to_thread(self.store.claim_source)
        if claimed:
            receipt = await asyncio.to_thread(self.fetcher, claimed["etag"], claimed["modified"])
            result = await asyncio.to_thread(self.store.save_snapshot, claimed, receipt)
        digest = await asyncio.to_thread(self.store.claim_enrichment)
        if digest:
            await self.enrich(digest)
            result["enriched"] = str(digest["id"])
        return result

    async def enrich(self, digest):
        bundle = digest["bundle"]
        candidates = [c for c in bundle["candidates"] if c.get("kind") != "major_issue"]
        for start in range(0, len(candidates), 5):
            if schedule.utcnow() >= digest["send_at"]:
                break
            group = candidates[start:start + 5]
            payload = trend_payload(group, digest["cutoff"].astimezone(schedule.KST))
            cached = (await asyncio.to_thread(self.store.cached_trends, digest["id"], group, payload)
                      if self.naver.credentials() else {})
            for candidate in group:
                if candidate["id"] in cached:
                    candidate["naver"] = cached[candidate["id"]]
            group = [candidate for candidate in group if candidate["id"] not in cached]
            if not group:
                continue
            payload = trend_payload(group, digest["cutoff"].astimezone(schedule.KST))
            receipt = await asyncio.to_thread(self.store.naver, digest["id"], "trend", payload, self.naver)
            results = receipt.get("data", {}).get("results", []) if receipt.get("ok") else []
            results = results if isinstance(results, list) else []
            for candidate in group:
                matches = [r for r in results if isinstance(r, dict) and r.get("title") == candidate["id"]
                           and r.get("keywords") == [candidate["title"]]]
                candidate["naver"] = (trend_summary(matches[0], payload) if len(matches) == 1 else
                                       {"state": "unavailable", "reason": receipt.get("error", "no_data")})
        semaphore, reads = asyncio.Semaphore(4), {}
        cached = {a["url"]: a for c in bundle["candidates"] if c.get("kind") == "major_issue" for a in c["articles"]}

        async def original(link):
            if link["url"] in cached:
                return cached[link["url"]]
            async with semaphore:
                if schedule.utcnow() >= digest["send_at"]:
                    return None
                receipt = await asyncio.to_thread(self.reader, link, self.store.company.settings)
            if not receipt.get("ok"):
                return None
            published = timestamp(receipt.get("published_at"))
            at = schedule.utcnow()
            if not published or not digest["cutoff"] - timedelta(hours=48) <= published <= at + timedelta(minutes=5):
                return None
            content = receipt.get("content", "")[:1200]
            if len(content) < 120:
                return None
            return as_json({"id": fingerprint([link["url"], content])[:32], "url": link["url"],
                            "publisher": receipt["publisher"], "published_at": published, "retrieved_at": at,
                            "content": content, "excerpt_truncated": receipt.get("excerpt_truncated", False)
                            or len(receipt.get("content", "")) > 1200,
                            "sha256": receipt.get("original_sha256"), "extraction": receipt.get("article_extraction")})

        async def enrich_candidate(candidate):
            links = list(candidate["news"])
            permitted = [link for link in links if article_source(link["url"], self.store.company.settings)]
            if len(permitted) < 2 and schedule.utcnow() < digest["send_at"]:
                receipt = await asyncio.to_thread(self.store.naver, digest["id"], "news",
                    {"query": candidate["title"], "display": 5, "sort": "date"}, self.naver)
                items = receipt.get("data", {}).get("items", []) if receipt.get("ok") else []
                for item in items[:5] if isinstance(items, list) else []:
                    try:
                        link = {"url": canonical_url(item["originallink"]), "title": clean(item["title"])[:240],
                                "publisher": "네이버 뉴스 검색"}
                        if not any(existing["url"] == link["url"] for existing in links):
                            links.append(link)
                    except (KeyError, ValueError, TypeError):
                        continue
                permitted = [link for link in links if article_source(link["url"], self.store.company.settings)]
            candidate["news"] = links[:8]
            for link in permitted[:2]:
                if link["url"] not in reads:
                    reads[link["url"]] = asyncio.create_task(original(link))
                article = await reads[link["url"]]
                if article:
                    candidate["articles"].append(article)

        await asyncio.gather(*(enrich_candidate(c) for c in candidates))
        await asyncio.to_thread(self.store.save_enrichment, digest, bundle)

    @activity.defn(name="company_trend_feed_collect")
    async def activity_tick(self):
        task = asyncio.create_task(self.tick())
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=10)
                activity.heartbeat()
            return await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @activity.defn(name="company_trend_feed_finalize")
    async def activity_finalize(self):
        result = await asyncio.to_thread(self.store.finalize)
        await asyncio.to_thread(self.store.cleanup)
        return result


class TrendFeedEditor:
    def __init__(self, company, provider=None):
        self.store = TrendFeedStore(company)
        self.provider = provider or provider_for(company)

    async def tick(self):
        call = await asyncio.to_thread(self.store.prepare_call)
        if not call:
            return {"state": "idle"}
        request = ProviderRequest.model_validate(call["request"])
        task = None
        try:
            task = asyncio.create_task(self.provider.run(request))
            while not task.done():
                await asyncio.wait({task}, timeout=10)
                if activity.in_activity():
                    activity.heartbeat({"request_id": request.request_id})
            response = ProviderResponse.model_validate(await task)
            await asyncio.to_thread(self.store.finish_call, call, response)
        except ProviderFault as exc:
            await asyncio.to_thread(self.store.fault_call, call, exc.code, exc.retry_after_seconds)
        except (ValueError, TypeError):
            await asyncio.to_thread(self.store.fault_call, call, "invalid_output")
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self.store.fault_call, call, "uncertain"))
            raise
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return {"state": "reviewed", "id": call["id"]}

    @activity.defn(name="company_trend_feed_edit")
    async def activity_tick(self):
        return await self.tick()
