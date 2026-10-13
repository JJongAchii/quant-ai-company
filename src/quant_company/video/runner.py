import asyncio
import base64
import contextlib
import io
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError
from temporalio import activity

from ..company import PolicyError
from ..contracts import ProviderAttachment, ProviderFault, ProviderRequest
from ..providers.client import RuntimeClient
from . import store as store_module
from .contracts import (
    VideoReview,
    VisualReview,
    digest,
    production_prompt,
    review_prompt,
    visual_review_prompt,
)
from .episode import (
    ScreenFix,
    apply_screen_fix,
    episode_prompt,
    fresh_library,
    plan_class,
    plan_findings,
    screen_fix_prompt,
    template,
)
from .motion import FPS as MOTION_FPS
from .motion import RENDERER_DIGEST, MotionRenderer
from .render import Renderer, RenderInProgress, korean_numbers, spoken, verify_artifacts
from .runway import RETAKE_RESERVE, RunwaySpeech, download_audio, speech_credits
from .store import UncertainEffect, VideoStore
from .youtube import YouTube

# A tick waits this long for a turn, then checks back: claude-runtime keeps running it under the same request ID.
VIDEO_MODEL_WAIT_SECONDS = 120
# Like an editor in a Claude session: rewrite until the checks pass, then keep the best version and report the rest.
PLAN_ATTEMPTS = 6
REVIEW_ROUNDS = 2
# A turn the runtime lost (uncertain: the CLI exited or its result went missing; timeout: the process limit) says nothing
# about the script. It is asked again as a new request after a pause and uses none of the attempts or rounds above, up to
# these limits (2026-10-13 PM: five CLI exits in a row used five of six attempts, so fifteen review concerns never got a
# new version).
RUNTIME_OUTCOMES = frozenset({'uncertain', 'timeout'})
PLAN_RUNTIME_RETRIES = 8
REVIEW_RUNTIME_RETRIES = 4
REVIEW_CHECKS = ('facts', 'numbers', 'conditions', 'coverage', 'readability')
INSPECT_ROUNDS = 3
REVIEW_PHASES = frozenset({'review', 'visual'})


class ModelRejected(ValueError):
    """An executed turn without a usable output (contract failure or process limit); the detail names the rule."""

    def __init__(self, code, detail):
        super().__init__(code)
        self.code, self.detail = code, detail


def written_attempts(qa):
    """Script attempts the model actually answered (a lost runtime turn is not one)."""
    return sum(a['outcome'] not in RUNTIME_OUTCOMES for a in qa.get('plan_attempts', []))


def render_key(job, takes, images):
    """Render directories are named by what they render, so an interrupted render resumes scene by scene and a changed
    plan, take, picture or renderer code gets a new directory (a committed attempt is never overwritten)."""
    return 'render-' + digest({'plan': job['plan'], 'takes': list(takes), 'template': template(job), 'fps': MOTION_FPS,
                               'renderer': RENDERER_DIGEST, **({'images': images} if images else {})})[:20]

def attempt_key(phase, number):
    return phase if number == 1 else f'{phase}-{number}'


def repair_feedback(base, findings):
    note = ('The previous version was rejected. Fix every item below and keep what already followed the rules:\n'
            + '\n'.join(f'- {f}' for f in findings[:25]))
    return f'{base}\n\n{note}' if base else note


def contract_detail(error):
    return '; '.join('.'.join(map(str, e['loc'])) + ': ' + e['msg']
                     for e in error.errors(include_url=False, include_input=False))[:1500]


class VideoFailure(ValueError):
    """A stage failure with an operator-facing reason code (store.REASONS); never carries URLs or secrets."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def free_gb(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free / 1e9


class VideoRunner:
    def __init__(self, company, provider=None, speech=None, renderer=None, youtube=None, images=None):
        self.store = VideoStore(company)
        self.settings = company.settings
        self._provider = provider
        self._images = images
        self.speech = speech or RunwaySpeech(self.settings.video_credentials_dir)
        self._renderer = renderer
        self._remote = None
        self.youtube = youtube or YouTube(self.settings)

    @property
    def images(self):
        if self._images is None:
            from .illustrations import ImageClient

            self._images = ImageClient(self.settings.video_image_runtime_url,
                                       self.settings.model_runtime_token.get_secret_value())
        return self._images

    def find_photos(self, job, items, folder):
        """Commons candidates for each photo request (network only); a failed lookup leaves that picture without one."""
        import hashlib

        from . import photos

        folder.mkdir(parents=True, exist_ok=True)
        found = {}
        for item in items:
            rows = []
            try:
                for row in photos.search(photos.query(item))[:8]:
                    if len(rows) >= photos.CANDIDATES:
                        break
                    try:
                        data = photos.download(row)
                    except Exception:  # noqa: BLE001 - one unreadable file: try the next candidate
                        continue
                    sha = hashlib.sha256(data).hexdigest()
                    name = f"{item.id}-photo{len(rows) + 1}-{sha[:10]}.jpg"
                    partial = folder / (name + '.partial')
                    partial.write_bytes(data)
                    partial.replace(folder / name)
                    rows.append({**row, 'file': name, 'sha256': sha, 'label': f"{item.id}-c{len(rows) + 1}"})
            except Exception as error:  # noqa: BLE001 - Commons unreachable: generated picture or library fallback
                rows = [{'error': f'{type(error).__name__}'[:80]}]
            found[item.id] = rows
        return found

    async def check_photos(self, job, items, found, folder, round_key):
        """Claude looks at every candidate in one call; the first suitable one per picture is used. No check, no photo."""
        from PIL import Image

        from . import photos
        from .illustrations import request_id

        rows = [(item, row) for item in items for row in found.get(item.id, []) if row.get('file')]
        if not rows:
            return {}
        attachments = []
        for n, (_, row) in enumerate(rows, 1):
            image = Image.open(folder / row['file'])
            image.thumbnail((1280, 720))
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=80)
            attachments.append(ProviderAttachment(name=f"photo-{n:02}.jpg", media_type='image/jpeg',
                                                  data=base64.b64encode(buffer.getvalue()).decode()))
        prompt = photos.check_prompt([(row['label'], item.subject, photos.query(item)) for item, row in rows])
        try:
            review = await self.model(job, f'visual-photos-{round_key[:8]}', prompt, VisualReview,
                                      'video_visual_review_v1', attachments)
        except ModelRejected:
            return {}
        rejected = {issue.scene for issue in review.issues}
        made = {}
        for item, row in rows:
            if item.id not in made and row['label'] not in rejected:
                made[item.id] = {'request_id': request_id(job, item), 'file': row['file'], 'sha256': row['sha256'],
                                 'kind': photos.KIND, 'subject': item.subject, 'author': row['author'],
                                 'license': row['license'], 'license_url': row['license_url'], 'source': row['page'],
                                 'commons_title': row['title'], 'width': 1920, 'height': 1080}
        return made

    async def illustrate(self, job, plan):
        """Story pictures before speech (owner, 2026-10-12): photo requests first get a real, free-licensed photo
        (one Commons lookup and one Claude check per plan), then one generated picture per tick for the rest.
        True while pictures are still being looked up, checked, made or awaited."""
        from . import photos
        from .illustrations import make_next, missing

        if not missing(job, plan):
            return False
        folder = self.settings.video_artifact_dir / str(job['id']) / 'illustrations'
        asked = [item for item in missing(job, plan) if photos.query(item)]
        # One lookup and one check per plan: keyed by every photo request, not by those still missing, so a picture
        # generated after a rejected photo does not start another round (2026-10-13 v2 ran three checks).
        round_key = digest([[i.id, i.subject, i.brief] for i in plan.illustrations if photos.query(i)]) if asked else None
        qa = dict(job.get('qa') or {})
        if asked and qa.get('photo_round') != round_key:
            found = await asyncio.to_thread(self.find_photos, job, asked, folder)
            qa.update(photo_round=round_key, photo_candidates=found, photo_checked=False)
            await asyncio.to_thread(self.store.update_qa, job, qa)
            await asyncio.to_thread(self.store.save, job, 'synthesizing')  # releases the lease for the next tick
            return True
        if asked and not qa.get('photo_checked'):
            chosen = await self.check_photos(job, asked, qa.get('photo_candidates') or {}, folder, round_key)
            qa.update(illustrations={**(qa.get('illustrations') or {}), **chosen}, photo_checked=True)
            await asyncio.to_thread(self.store.update_qa, job, qa)
            await asyncio.to_thread(self.store.save, job, 'synthesizing')
            return True
        made, waiting = await make_next(job, plan, self.images, self.settings, folder)
        qa = dict(job.get('qa') or {})
        qa['illustrations'] = made
        await asyncio.to_thread(self.store.update_qa, job, qa)
        if waiting:
            await asyncio.to_thread(self.store.wait, job, 30, 'illustration_pending')
        else:
            await asyncio.to_thread(self.store.save, job, 'synthesizing')
        return True

    def renderer_for(self, job):
        if self._renderer is not None:
            return self._renderer
        return MotionRenderer(self.settings) if template(job) == 'motion-v2' else Renderer(self.settings)

    @property
    def provider(self):
        # Created on first model call: speech, render and upload stages never need the runtime token.
        if self._provider is None:
            # Wait past the runtime's video process limit so a long plan returns instead of turning uncertain.
            self._provider = RuntimeClient(self.settings.video_model_runtime_url,
                self.settings.model_runtime_token.get_secret_value(), timeout_seconds=VIDEO_MODEL_WAIT_SECONDS,
                expected_provider="claude")
        return self._provider

    async def effect(self, job, key, request, kind, operation, cost=0):
        old = await asyncio.to_thread(self.store.begin_effect, job, key, request, kind, cost)
        if old is not None:
            return old
        result = await operation()
        await asyncio.to_thread(self.store.finish_effect, job, key, result)
        return result

    async def model(self, job, key, prompt, cls, contract=None, attachments=()):
        phase = key.split('-')[0]
        policy = job["policy"]
        # Reviews may think longer than writing; jobs frozen before the split use their single effort for both.
        effort = (policy.get("review_effort", policy.get("effort", "high")) if phase in REVIEW_PHASES
                  else policy.get("effort", "high"))
        request = ProviderRequest(request_id=f"video-{job['id']}-{key}", model=policy["model"],
                                  reasoning_effort=effort, prompt=prompt,
                                  output_contract=contract or f"video_{phase}_v1", attachments=list(attachments))
        failed = await asyncio.to_thread(self.store.effect_receipt, job, key, 'failed')
        if failed:
            raise ModelRejected(failed.get('code', 'invalid_output'), failed.get('detail', ''))

        async def invoke():
            try:
                response = await self.provider.run(request)
            except ProviderFault as error:
                # Both are reported before any inference: a shared lane or an exhausted subscription window.
                if error.code in {'busy', 'quota'}:
                    await asyncio.to_thread(self.store.reject_busy_model, job, key, error.code)
                # uncertain = the turn left no usable result (CLI exit, lost result, 2026-10-12 PM). Owner: production
                # never blocks and the call count does not matter, so it is a failed attempt; the next attempt uses a
                # new request ID and the runtime receipt of this one is kept as it is.
                elif error.code in {'invalid_output', 'timeout', 'uncertain'}:
                    detail = str(error.message)[:2000]
                    await asyncio.to_thread(self.store.fail_effect, job, key, {'code': error.code, 'detail': detail})
                    raise ModelRejected(error.code, detail) from None
                raise
            decision = response.decision
            if (response.provider != "claude" or response.usage.get("actual_model") != request.model
                    or response.request_id != request.request_id or len(decision.artifacts) != 1
                    or decision.status != 'complete' or decision.tools or decision.delegations or decision.messages
                    or decision.memories or decision.follow_up or response.web_searches):
                raise ValueError("Video model response identity mismatch")
            try:
                result = cls.model_validate_json(response.decision.artifacts[0].content)
            except ValidationError as error:
                detail = contract_detail(error)
                await asyncio.to_thread(self.store.fail_effect, job, key, {'code': 'invalid_output', 'detail': detail})
                raise ModelRejected('invalid_output', detail) from None
            return {"output": result.model_dump(mode="json"), "usage": response.usage,
                    "provider": response.provider, "request_id": response.request_id}

        result = await self.effect(job, key, request.model_dump(mode="json"), "model", invoke)
        return cls.model_validate(result["output"])

    async def tick(self):
        await asyncio.to_thread(self.store.prune)
        if self.store.enabled():
            await asyncio.to_thread(self.store.skip_notices)
            await asyncio.to_thread(self.store.sync_deliveries)
            await asyncio.to_thread(self.store.late_notices)
            free = free_gb(self.settings.video_artifact_dir)
            if free < self.settings.video_min_free_gb:
                await asyncio.to_thread(self.store.hold_for_disk, free)
                return {"state": "disk_low", "free_gb": round(free, 1)}
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
            await asyncio.to_thread(self.store.alert, job)
        except ProviderFault as error:
            if error.code in {'busy', 'quota', 'pending', 'unavailable'}:
                await asyncio.to_thread(self.store.defer, job, error.retry_after_seconds or 30, error.code)
            else:
                uncertain = error.code == 'uncertain' or await asyncio.to_thread(self.store.has_uncertain_effect, job)
                await asyncio.to_thread(self.store.save, job, 'uncertain' if uncertain else "blocked", error="model_" + error.code)
                await asyncio.to_thread(self.store.alert, job)
        except (ValueError, OSError, TimeoutError, httpx.HTTPError, PolicyError) as error:
            # Record a bounded diagnostic code. Exception strings may contain signed media URLs or credentials.
            uncertain = await asyncio.to_thread(self.store.has_uncertain_effect, job)
            code = error.code if isinstance(error, VideoFailure) else "video_stage_failed:" + job["state"]
            await asyncio.to_thread(self.store.save, job, 'uncertain' if uncertain else "blocked",
                                    error="external_effect_requires_reconciliation" if uncertain else code)
            await asyncio.to_thread(self.store.alert, job)
        return {"state": (await asyncio.to_thread(self.store.get, job["id"]))["state"], "job_id": str(job["id"])}

    async def write_plan(self, job):
        """One attempt per tick. Findings go back as revision feedback; after the last attempt the version with the
        fewest findings is produced anyway and its remaining findings travel with the files."""
        qa = dict(job.get('qa') or {})
        attempts = list(qa.get('plan_attempts', []))
        number = len(attempts) + 1
        key = attempt_key('plan', number)
        feedback = qa.get('feedback', job['feedback'])
        library = None
        if template(job) == 'motion-v2':
            # Kept as ordered pairs: JSONB reorders object keys, and the shelf order is part of the frozen prompt.
            library = dict(qa['library']) if qa.get('library_key') == key else None
            if library is None:
                # Images shown (thumbnail or scene) in the last 7 days are not offered again. Frozen per attempt, so
                # asking claude-runtime again for a still-running turn sends the identical request.
                usage = await asyncio.to_thread(self.store.asset_usage)
                library = fresh_library(MotionRenderer(self.settings).library, usage, store_module.utcnow())
                qa['library'], qa['library_key'] = [[k, v] for k, v in library.items()], key
                # Story pictures are offered only while the image runtime is configured; frozen with the shelf.
                qa['illustrations_offered'] = bool(self.settings.video_image_runtime_url)
                await asyncio.to_thread(self.store.update_qa, job, qa)
            prompt, contract = episode_prompt(job['source'], feedback, library,
                                              illustrations=qa.get('illustrations_offered', False),
                                              lessons=job['policy'].get('lessons', ())), 'video_episode_v1'
        else:
            prompt, contract = production_prompt(job['source'], feedback), None
        try:
            plan = await self.model(job, key, prompt, plan_class(job), contract)
            findings = plan_findings(job, plan, library, photos=qa.get('illustrations_offered', False))
            attempts.append({'key': key, 'outcome': 'check_failed' if findings else 'passed', 'findings': findings[:30]})
        except ModelRejected as rejected:
            plan, findings = None, [f'Output contract: {rejected.detail}' if rejected.detail else f'Model {rejected.code}']
            attempts.append({'key': key, 'outcome': rejected.code, 'findings': findings})
        qa['plan_attempts'] = attempts
        if plan is not None and not findings:
            qa['plan_findings'] = []
            await asyncio.to_thread(self.store.save, job, 'reviewing', plan=plan.model_dump(mode='json'), qa=qa)
            return
        lost = sum(a['outcome'] in RUNTIME_OUTCOMES for a in attempts)
        if written_attempts(qa) < PLAN_ATTEMPTS and lost < PLAN_RUNTIME_RETRIES:
            if attempts[-1]['outcome'] in RUNTIME_OUTCOMES:
                # Same feedback as the lost turn; only the request is new.
                await asyncio.to_thread(self.store.save, job, 'queued', qa=qa)
                await asyncio.to_thread(self.store.wait, job, min(180, 30 * lost), 'model_retry')
                return
            qa['feedback'] = repair_feedback(job['feedback'], findings)
            await asyncio.to_thread(self.store.save, job, 'queued', qa=qa)
            return
        usable = [a for a in attempts if a['outcome'] in {'passed', 'check_failed'}]
        if not usable:
            raise VideoFailure('plan_contract_failed')
        best = min(usable, key=lambda a: len(a['findings']))
        plan = plan_class(job).model_validate((await asyncio.to_thread(self.store.effect_receipt, job, best['key'],
                                                                       'completed'))['output'])
        qa['plan_findings'] = best['findings']
        await asyncio.to_thread(self.store.save, job, 'reviewing', plan=plan.model_dump(mode='json'), qa=qa)

    async def review_plan(self, job):
        """The reviewer's concerns are fixed by another script version; production continues either way."""
        qa = dict(job.get('qa') or {})
        rounds = list(qa.get('review_rounds', []))
        key = attempt_key('review', len(rounds) + 1)
        plan = plan_class(job).model_validate(job['plan'])
        try:
            review = await self.model(job, key, review_prompt(job['source'], plan), VideoReview)
            concerns = list(review.concerns) + [f'{name} check failed' for name in REVIEW_CHECKS if not getattr(review, name)]
        except ModelRejected as rejected:
            lost = sum(r.get('outcome') in RUNTIME_OUTCOMES for r in rounds)
            if rejected.code in RUNTIME_OUTCOMES and lost < REVIEW_RUNTIME_RETRIES:
                # The runtime lost the review turn: the same script is reviewed again, nothing is rewritten.
                rounds.append({'key': key, 'passed': False, 'outcome': rejected.code, 'concerns': []})
                qa['review_rounds'] = rounds
                await asyncio.to_thread(self.store.save, job, 'reviewing', qa=qa)
                await asyncio.to_thread(self.store.wait, job, min(180, 30 * (lost + 1)), 'model_retry')
                return
            review, concerns = None, [f'Review output contract: {rejected.detail or rejected.code}']
        rounds.append({'key': key, 'passed': not concerns, 'concerns': concerns[:20]})
        qa['review_rounds'] = rounds
        reviewed = sum(r.get('outcome') not in RUNTIME_OUTCOMES for r in rounds)
        if concerns and reviewed < REVIEW_ROUNDS and written_attempts(qa) < PLAN_ATTEMPTS:
            qa['feedback'] = repair_feedback(job['feedback'], qa.get('plan_findings', []) + concerns)
            await asyncio.to_thread(self.store.save, job, 'queued', qa=qa)
            return
        qa['review_findings'] = concerns
        await asyncio.to_thread(self.store.save, job, 'synthesizing',
                                review=review.model_dump(mode='json') if review else None, qa=qa)

    async def takes(self, job, plan):
        """(effect key, Runway request) per scene. A scene flagged as misread is re-recorded in Korean number words."""
        retake = set((job.get('qa') or {}).get('retake', []))
        result = []
        for index, scene in enumerate(plan.scenes):
            text = korean_numbers(scene.narration) if getattr(scene, 'id', None) in retake else spoken(scene.narration)
            request = {"text": text, "voice": job["policy"]["voice"], "model": job["policy"]["speech_model"]}
            result.append((await asyncio.to_thread(self.store.take_key, job, index, request), request))
        return result

    async def fetch_audio(self, job, takes, directory):
        paths = []
        for index, (key, _request) in enumerate(takes):
            receipt = await asyncio.to_thread(self.store.effect_receipt, job, key, 'completed')
            if not receipt:
                raise ValueError("Missing narration receipt")
            url = await self.speech.completed(receipt["taskId"])
            path = directory / f"speech-{index:02}.mp3"
            await asyncio.to_thread(download_audio, url, path)
            paths.append(path)
        return paths

    def remote(self):
        if self._remote is None:
            from .remote import RemoteRender

            self._remote = RemoteRender(self.settings)
        return self._remote

    async def render_remote(self, job, plan, takes, directory, texts, qa):
        """One Fargate task per attempt, asked again on later ticks like a model turn. Spot reclaims resume from the
        scenes already in S3; after the last attempt the episode renders here (it is always produced).

        The attempt is recorded before RunTask, so a launch whose answer was lost is asked again with the same client
        token (ECS starts at most one task per token) instead of being replayed as a new paid task."""
        from .remote import RUNNING, STOPPING, ambiguous

        state = dict(qa.get('render') or {})
        if state.get('key') != directory.name:
            state = {'key': directory.name, 'attempts': []}
        attempts = state['attempts'] = [dict(a) for a in state.get('attempts', [])]
        qa['render'] = state
        remote, limit = self.remote(), self.settings.video_render_attempts
        current = attempts[-1] if attempts and 'outcome' not in attempts[-1] else None

        async def wait(seconds, reason):
            await asyncio.to_thread(self.store.update_qa, job, qa)
            await asyncio.to_thread(self.store.wait, job, seconds, reason)

        if current and current.get('task'):
            try:
                status = await asyncio.to_thread(remote.status, current['task'])
            except Exception as error:  # noqa: BLE001 - AWS unreachable: the task keeps running; ask again later
                current['errors'] = current.get('errors', 0) + 1
                if current['errors'] < 10:
                    await wait(60, 'render_remote_status')
                    return None
                # Lost track of the task: it stops itself at its time limit; the next attempt resumes from S3.
                current.update(outcome='error', reason=f'status: {type(error).__name__}')
                status = None
            if status is None:
                pass
            elif status['status'] in STOPPING and status['exit'] is None:
                await wait(30, 'render_remote_stopping')
                return None
            elif status['status'] in RUNNING:
                started = datetime.fromisoformat(current['launched_at'])
                if (datetime.now(UTC) - started).total_seconds() < (self.settings.video_render_timeout_minutes + 10) * 60:
                    await wait(60, 'render_remote')
                    return None
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(remote.stop, current['task'], 'render time limit')
                current.update(outcome='timeout')
            elif status['exit'] == 0:  # STOPPED, or still deprovisioning after the render task exited cleanly
                try:
                    artifacts = await asyncio.to_thread(remote.collect, job, directory.name, directory)
                except Exception as error:  # noqa: BLE001 - missing or corrupt result: one failed attempt
                    current.update(outcome='failed', reason=f'collect: {type(error).__name__}: {error}'[:300])
                else:
                    current.update(outcome='completed', finished_at=datetime.now(UTC).isoformat())
                    code = str(artifacts.get('renderer') or 'unknown')
                    qa['render_findings'] = [] if code == RENDERER_DIGEST else [
                        f"Render image code {code[:12]} differs from the worker's {RENDERER_DIGEST[:12]}: rebuild the render image"]
                    return artifacts
            else:
                current.update(outcome='failed', stop_code=status.get('stop_code'), reason=status.get('reason'))
            current = None
        if current is None and len(attempts) < limit:
            current = {'task': None, 'launched_at': datetime.now(UTC).isoformat()}
            attempts.append(current)
        if current is not None:
            try:
                if not await asyncio.to_thread(remote.inputs_ready, job, directory.name):
                    paths = await self.fetch_audio(job, takes, directory)
                    await asyncio.to_thread(remote.upload_inputs, job, plan, paths, texts, directory.name)
                await asyncio.to_thread(self.store.update_qa, job, qa)  # the receipt precedes the paid call
                current['task'] = await asyncio.to_thread(remote.launch, job, directory.name, len(attempts))
                current['launched_at'] = datetime.now(UTC).isoformat()
                await wait(60, 'render_remote')
                return None
            except Exception as error:  # noqa: BLE001 - refused (definitive) or unanswered (ask again, same token)
                current['errors'] = current.get('errors', 0) + 1
                if ambiguous(error) and current['errors'] < 5:
                    await wait(60, 'render_remote_launch')
                    return None
                current.update(outcome='error', reason=f'{type(error).__name__}: {error}'[:300])
        if len(attempts) >= limit and all('outcome' in a for a in attempts):
            qa['render_local'] = True
        await wait(5 if qa.get('render_local') else 30,
                   'render_local_fallback' if qa.get('render_local') else 'render_remote_retry')
        return None

    async def inspect_video(self, job):
        """The final editor: Claude looks at the rendered frames (and the speech-to-text findings), card-level
        problems are fixed and re-rendered, misread scenes re-recorded; after the last round the files go out anyway."""
        qa = dict(job.get('qa') or {})
        rounds = list(qa.get('inspections', []))
        artifacts = job['artifacts']
        directory = verify_artifacts(artifacts, self.settings.video_artifact_dir)
        review = artifacts.get('review') or {'frames': [], 'audio': {}}
        frames = [f for f in review['frames'] if (directory / f['file']).is_file()]
        plan = plan_class(job).model_validate(job['plan'])
        key = attempt_key('visual', len(rounds) + 1)
        issues, visual = [], None
        if frames and template(job) == 'motion-v2':
            attachments = [ProviderAttachment(name=f"frame-{n:02}" + Path(f['file']).suffix,
                                              media_type='image/png' if f['file'].endswith('.png') else 'image/jpeg',
                                              data=base64.b64encode((directory / f['file']).read_bytes()).decode())
                           for n, f in enumerate(frames, 1)]
            prompt = visual_review_prompt(job['source'], plan, [(f['scene'], f'image {n}') for n, f in enumerate(frames, 1)],
                                          review['audio'])
            try:
                visual = await self.model(job, key, prompt, VisualReview, 'video_visual_review_v1', attachments)
                issues = [i.model_dump() for i in visual.issues]
            except ModelRejected as rejected:
                issues = [{'scene': 'thumbnail', 'kind': 'other', 'detail': f'Visual review unavailable: {rejected.detail}',
                           'fix': 'none'}]
        heard = sorted(review['audio'])
        retake = sorted({i['scene'] for i in issues if i['kind'] == 'audio_text'} | (set(heard) if visual is None else set()))
        screen = [i for i in issues if i['kind'] != 'audio_text' and i['fix'] != 'none']
        rounds.append({'key': key, 'issues': issues, 'heard': review['audio'], 'findings': artifacts.get('findings', [])})
        qa['inspections'] = rounds
        if (screen or retake) and len(rounds) < INSPECT_ROUNDS:
            fixed = plan
            if screen and template(job) == 'motion-v2':
                fix_key = attempt_key('fix', len(rounds))
                try:
                    patch = await self.model(job, fix_key, screen_fix_prompt(job['source'], plan, screen), ScreenFix,
                                             'video_screen_fix_v1')
                    reverted = []
                    library = getattr(self.renderer_for(job), 'library', None)
                    library = library if isinstance(library, dict) else MotionRenderer(self.settings).library
                    fixed = apply_screen_fix(plan, patch, library, reverted)
                    if reverted:
                        rounds[-1]['fix_reverted'] = reverted  # an image outside the library: the scene stays
                except (ModelRejected, ValueError) as error:
                    rounds[-1]['fix_error'] = str(getattr(error, 'detail', error))[:500]
            qa['retake'] = sorted(set(qa.get('retake', [])) | set(retake))
            changed = fixed.model_dump(mode='json') != plan.model_dump(mode='json')
            if changed or retake:
                qa['inspection_findings'] = []
                await asyncio.to_thread(self.store.save, job, 'synthesizing' if retake else 'rendering',
                                        plan=fixed.model_dump(mode='json'), qa=qa)
                return
        qa['inspection_findings'] = [f"{i['scene']}: {i['detail']}" for i in issues] + (
            [f'{scene}: speech-to-text mismatch' for scene in heard] if visual is None else [])
        await asyncio.to_thread(self.store.update_qa, job, qa)
        job = {**job, 'qa': qa}
        if job['policy'].get('delivery', 'youtube') == 'slack':
            # Default: the owner receives the files in the brief's thread and uploads them himself.
            await asyncio.to_thread(self.store.deliver, job, artifacts)
        else:
            await asyncio.to_thread(self.store.save, job, "uploading")

    async def step(self, job):
        state = job["state"]
        if state == "queued":
            await self.write_plan(job)
        elif state == "reviewing":
            await self.review_plan(job)
        elif state == "synthesizing":
            plan = plan_class(job).model_validate(job["plan"])
            if (template(job) == 'motion-v2' and getattr(plan, 'illustrations', None)
                    and self.settings.video_image_runtime_url and await self.illustrate(job, plan)):
                return
            # Account lookup does not spend credits. Check the entire expected episode before submission.
            # Runway unavailable or logged out stops here with an alert; there is no paid API fallback.
            try:
                account = await self.speech.account(job["policy"]["workspace_id"])
            except ValueError:
                raise VideoFailure('runway_account') from None
            except Exception:  # noqa: BLE001 - MCP/OAuth/transport errors all mean "no speech service"
                raise VideoFailure('runway_unavailable') from None
            takes = await self.takes(job, plan)
            pending = [speech_credits(request['text']) for key, request in takes if not await asyncio.to_thread(
                self.store.effect_receipt, job, key, 'completed')]
            expected = sum(pending) + math.ceil(len(pending) * RETAKE_RESERVE)
            if expected > account["credits"]["total"]:
                raise VideoFailure('runway_balance')
            if not await asyncio.to_thread(self.store.budget_available, job, expected):
                raise VideoFailure('credit_cap')
            # One scene per tick, retaining its task ID before polling or downloading.
            for key, request in takes:
                if await asyncio.to_thread(self.store.effect_receipt, job, key, 'completed'):
                    continue
                await self.effect(job, key, request, "speech", lambda request=request: self.speech.submit(**request),
                                  cost=speech_credits(request["text"]))
                await asyncio.to_thread(self.store.save, job, "synthesizing")
                return
            await asyncio.to_thread(self.store.save, job, "rendering")
        elif state == "rendering":
            plan = plan_class(job).model_validate(job["plan"])
            takes = await self.takes(job, plan)
            # Made pictures are part of what is rendered: a remade or fallen-back picture renders afresh.
            images = {k: v.get('sha256') or v.get('failed') for k, v in
                      ((job.get('qa') or {}).get('illustrations') or {}).items()}
            directory = self.settings.video_artifact_dir / str(job["id"]) / render_key(job, [k for k, _ in takes], images)
            directory.mkdir(parents=True, exist_ok=True)
            qa = dict(job.get('qa') or {})
            texts = [r['text'] for _, r in takes] if qa.get('retake') else None
            renderer = self.renderer_for(job)
            if (self.settings.video_render_backend == 'remote' and template(job) == 'motion-v2'
                    and not qa.get('render_local')):
                artifacts = await self.render_remote(job, plan, takes, directory, texts, qa)
                if artifacts is None:
                    return
                library = MotionRenderer(self.settings).library
            else:
                paths = await self.fetch_audio(job, takes, directory)
                try:
                    artifacts = await asyncio.to_thread(renderer.render, job, plan, directory, paths,
                                                        **({'texts': texts} if texts else {}))
                except RenderInProgress:
                    await asyncio.to_thread(self.store.wait, job, 120, 'render_in_progress')
                    return
                library = getattr(renderer, 'library', None) or {}
            await asyncio.to_thread(self.store.record_assets, job, plan, library)
            await asyncio.to_thread(self.store.save, job, 'inspecting', artifacts=artifacts,
                                    artifact_digest=artifacts['digest'], qa=qa)
        elif state == "inspecting":
            await self.inspect_video(job)
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
