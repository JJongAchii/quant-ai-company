import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from temporalio import activity

from ..company import PolicyError
from ..contracts import ProviderFault, ProviderRequest
from ..execution import provider_for
from .contracts import VideoPlan, VideoReview, digest, production_prompt, review_prompt, validate_plan
from .render import Renderer, spoken, verify_artifacts
from .runway import RunwaySpeech, download_audio
from .store import UncertainEffect, VideoStore
from .youtube import YouTube


class VideoRunner:
    def __init__(self, company, provider=None, speech=None, renderer=None, youtube=None):
        self.store = VideoStore(company)
        self.settings = company.settings
        self.provider = provider or provider_for(company)
        self.speech = speech or RunwaySpeech(self.settings.video_credentials_dir)
        self.renderer = renderer or Renderer(self.settings)
        self.youtube = youtube or YouTube(self.settings)

    async def effect(self, job, key, request, kind, operation, cost=0):
        old = await asyncio.to_thread(self.store.begin_effect, job, key, request, kind, cost)
        if old is not None:
            return old
        result = await operation()
        await asyncio.to_thread(self.store.finish_effect, job, key, result)
        return result

    async def model(self, job, phase, prompt, cls):
        request = ProviderRequest(request_id=f"video-{job['id']}-{phase}", model=job["policy"]["model"],
                                  reasoning_effort="high", prompt=prompt, output_contract=f"video_{phase}_v1")

        async def invoke():
            try:
                response = await self.provider.run(request)
            except ProviderFault as error:
                if error.code == 'busy':
                    await asyncio.to_thread(self.store.reject_busy_model, job, phase)
                raise
            decision = response.decision
            if (response.request_id != request.request_id or len(decision.artifacts) != 1
                    or decision.status != 'complete' or decision.tools or decision.delegations or decision.messages
                    or decision.memories or decision.follow_up or response.web_searches):
                raise ValueError("Video model response identity mismatch")
            result = cls.model_validate_json(response.decision.artifacts[0].content)
            return {"output": result.model_dump(mode="json"), "usage": response.usage,
                    "provider": response.provider, "request_id": response.request_id}

        result = await self.effect(job, phase, request.model_dump(mode="json"), "model", invoke)
        return cls.model_validate(result["output"])

    async def tick(self):
        if self.store.enabled():
            await asyncio.to_thread(self.store.late_notices)
        for row in await asyncio.to_thread(self.store.pending_notices):
            if self.store.enabled():
                await asyncio.to_thread(self.store.notice, row)
        job = await asyncio.to_thread(self.store.claim)
        if not job:
            return {"state": "idle"}
        try:
            if digest(job["source"]) != job["source_digest"]:
                raise ValueError("Frozen briefing source changed")
            if not self.store.allowed(job):
                raise PolicyError('Video production account or source policy changed')
            await self.step(job)
        except UncertainEffect:
            await asyncio.to_thread(self.store.save, job, "uncertain", error="external_effect_requires_reconciliation")
        except ProviderFault as error:
            if error.code == 'busy':
                await asyncio.to_thread(self.store.defer, job, error.retry_after_seconds)
            else:
                uncertain = await asyncio.to_thread(self.store.has_uncertain_effect, job)
                await asyncio.to_thread(self.store.save, job, 'uncertain' if uncertain else "blocked", error="model_" + error.code)
        except (ValueError, OSError, TimeoutError, httpx.HTTPError, PolicyError):
            # Record a bounded diagnostic code. Exception strings may contain signed media URLs or credentials.
            uncertain = await asyncio.to_thread(self.store.has_uncertain_effect, job)
            await asyncio.to_thread(self.store.save, job, 'uncertain' if uncertain else "blocked", error="video_stage_failed:" + job["state"])
        return {"state": (await asyncio.to_thread(self.store.get, job["id"]))["state"], "job_id": str(job["id"])}

    async def step(self, job):
        state = job["state"]
        if state == "queued":
            plan = await self.model(job, "plan", production_prompt(job["source"], job["feedback"]), VideoPlan)
            validate_plan(plan, job["source"])
            await asyncio.to_thread(self.store.save, job, "reviewing", plan=plan.model_dump(mode="json"))
        elif state == "reviewing":
            plan = VideoPlan.model_validate(job["plan"])
            review = await self.model(job, "review", review_prompt(job["source"], plan), VideoReview)
            await asyncio.to_thread(self.store.save, job, "synthesizing" if review.passed() else "blocked",
                                    review=review.model_dump(mode="json"), error=None if review.passed() else "adaptation_review_failed")
        elif state == "synthesizing":
            plan = VideoPlan.model_validate(job["plan"])
            # Account lookup does not spend credits. Check the entire expected episode before submission.
            account = await self.speech.account(job["policy"]["workspace_id"])
            with self.store.db.transaction() as conn:
                finished = {int(str(r['id']).rsplit('-', 1)[-1]) for r in conn.execute(
                    "SELECT id FROM video_effects WHERE job_id=%s AND kind='speech' AND state='completed'", (job['id'],)).fetchall()}
            expected = sum((len(spoken(s.narration))+49)//50 for i,s in enumerate(plan.scenes) if i not in finished)
            if expected > account["credits"]["total"] or not await asyncio.to_thread(self.store.budget_available, job, expected):
                raise ValueError("Insufficient narration budget")
            # One scene per tick, retaining its task ID before polling or downloading.
            for index, scene in enumerate(plan.scenes):
                request = {"text": spoken(scene.narration), "voice": job["policy"]["voice"],
                           "model": job["policy"]["speech_model"]}
                key = f"speech-{index}"
                with self.store.db.transaction() as conn:
                    old = conn.execute("SELECT receipt FROM video_effects WHERE id=%s AND state='completed'",
                                       (str(job["id"])+":"+key,)).fetchone()
                if old:
                    continue
                await self.effect(job, key, request, "speech", lambda request=request: self.speech.submit(**request),
                                  cost=(len(request["text"])+49)//50)
                await asyncio.to_thread(self.store.save, job, "synthesizing")
                return
            await asyncio.to_thread(self.store.save, job, "rendering")
        elif state == "rendering":
            plan = VideoPlan.model_validate(job["plan"])
            # A cancelled render thread may still finish. It must never overwrite a committed attempt.
            directory = self.settings.video_artifact_dir / str(job["id"]) / str(uuid4())
            directory.mkdir(parents=True, exist_ok=True)
            paths = []
            for index, _scene in enumerate(plan.scenes):
                with self.store.db.transaction() as conn:
                    receipt = conn.execute("SELECT receipt FROM video_effects WHERE id=%s AND state='completed'",
                                           (str(job["id"])+f":speech-{index}",)).fetchone()
                if not receipt:
                    raise ValueError("Missing narration receipt")
                url = await self.speech.completed(receipt["receipt"]["taskId"])
                path = directory / f"speech-{index:02}.mp3"
                await asyncio.to_thread(download_audio, url, path)
                paths.append(path)
            artifacts = await asyncio.to_thread(self.renderer.render, job, plan, directory, paths)
            await asyncio.to_thread(self.store.save, job, "uploading", artifacts=artifacts, artifact_digest=artifacts["digest"])
        elif state == "uploading":
            verify_artifacts(job["artifacts"], self.settings.video_artifact_dir)
            if not job["upload_session"]:
                async def initiate():
                    url = await self.youtube.initiate(job)
                    await asyncio.to_thread(self.store.checkpoint, job, upload_session=url)
                    return {"session": url}

                receipt = await self.effect(job, "upload-session", {"digest": job["artifact_digest"]}, "upload", initiate)
                await asyncio.to_thread(self.store.checkpoint, job, upload_session=receipt["session"])
                job["upload_session"] = receipt["session"]
            if not job["youtube_id"]:
                identity = await self.youtube.upload(job)
                await asyncio.to_thread(self.store.checkpoint, job, youtube_id=identity)
                job["youtube_id"] = identity
            current = await self.youtube.video(job['youtube_id'])
            if (current['status']['privacyStatus'] != 'private'
                    or current['snippet']['channelId'] != job['policy']['youtube_channel']):
                raise ValueError('Uploaded video visibility or channel differs from policy')
            processing = current.get('processingDetails', {}).get('processingStatus')
            if processing == 'processing':
                await asyncio.to_thread(self.store.save, job, 'uploading')
                return
            if processing != 'succeeded':
                raise ValueError('YouTube processing has not succeeded')
            await self.effect(job, "thumbnail", {"digest": job["artifact_digest"], "video": job["youtube_id"]},
                              "youtube-write", lambda: self.youtube.thumbnail(job))
            await self.effect(job, "captions", {"digest": job["artifact_digest"], "video": job["youtube_id"]},
                              "youtube-write", lambda: self.youtube.captions(job))
            await asyncio.to_thread(self.store.save, job, "awaiting_approval")
            await asyncio.to_thread(self.store.notice, job)
        elif state == "approved":
            if not job["approved_at"] or datetime.now(UTC) >= job["publish_deadline"]:
                await asyncio.to_thread(self.store.save, job, "expired")
                return
            if (self.settings.video_youtube_channel_id != job["policy"]["youtube_channel"]
                    or self.settings.video_runway_workspace_id != job["policy"]["workspace_id"]):
                raise ValueError("Production account policy changed")
            verify_artifacts(job["artifacts"], self.settings.video_artifact_dir)
            if job['artifact_digest'] != job['artifacts']['digest']:
                raise ValueError('Approval artifact binding changed')
            await self.effect(job, "publish", {"video": job["youtube_id"], "digest": job["artifact_digest"],
                              "approved_by": job["approved_by"]}, "youtube-write", lambda: self.youtube.publish(job))
            await asyncio.to_thread(self.store.save, job, "published")

    @activity.defn(name="company_video_tick")
    async def activity_tick(self):
        task = asyncio.create_task(self.tick())
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=5)
                activity.heartbeat()
            return await task
        finally:
            if not task.done():
                task.cancel()
