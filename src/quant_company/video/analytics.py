"""Performance analytics of the owner's public uploads (VIDEO_ANALYTICS_ENABLED). Each tick: sync uploads, apply new
reach reports, take due fixed-window snapshots, then (only while no episode is in production) the three-day card
with a Claude diagnosis for an outlier of its kind, its seven-day update and the Monday/Thursday channel strategy
report whose strategies the owner adopts in Slack."""

import asyncio
import base64
import hashlib
import logging
from datetime import UTC, date, datetime, time, timedelta

import httpx
from pydantic import ValidationError
from temporalio import activity

from ..company import PolicyError, as_json
from ..contracts import ProviderAttachment, ProviderFault, ProviderRequest
from ..providers.client import RuntimeClient
from .analytics_contracts import (
    RETRY_NOTE,
    StrategyReport,
    VideoAnswer,
    VideoPerformance,
    answer_prompt,
    performance_prompt,
    strategy_prompt,
)
from .analytics_metrics import (
    KST,
    OUTLIER,
    OUTLIER_MIN,
    PT,
    hook_retention,
    in_series,
    medians,
    outlier,
    parse_title,
    report_stats,
    scene_drops,
    title_hook,
    window,
)
from .analytics_slack import (
    EDITIONS,
    EXPIRED_TEXT,
    WAIT_TEXT,
    answer_text,
    card_text,
    detail_text,
    failed_answer_text,
    failed_report_text,
    report_detail_text,
    report_texts,
    update_text,
)
from .analytics_store import REACH_GRACE_DAYS, REPORT_TYPES, AnalyticsStore

LOG = logging.getLogger(__name__)
BASELINE_KEYS = ("impressions", "ctr", "views", "avg_view_pct", "hook_30s", "subscribers", "relative_retention")
# A snapshot taken this many days after its window (a backfill) feeds baselines and the report, but posts no card nor
# counts as new in a report period.
REPORT_DAYS = REACH_GRACE_DAYS + 2
SNAPSHOTS_PER_TICK = 20
MODEL_WAIT_SECONDS = 120
MAX_THUMBNAILS = 12
PROMPT_LIMIT = 85000
# The newest measured videos a question under a report sees beside the report, within ProviderRequest's 90,000.
THREAD_VIDEOS = 40
# The channel strategy report: Monday and Thursday from 09:00 KST, caught up within two days if the worker was down
# (a catch-up ends before the next report starts, so two never overlap).
STRATEGY_WEEKDAYS, STRATEGY_HOUR, STRATEGY_CATCH_UP = (0, 3), 9, timedelta(hours=48)


def utcnow():
    return datetime.now(UTC)


def report_start(day) -> datetime:
    return datetime.combine(day, time(STRATEGY_HOUR), KST)


def report_day(at) -> date | None:
    """The newest report day whose start is at or before `at`, while it can still be caught up; else None."""
    days = (at.astimezone(KST).date() - timedelta(days=n) for n in range(7))
    day = next(d for d in days if d.weekday() in STRATEGY_WEEKDAYS and report_start(d) <= at)
    return day if at - report_start(day) < STRATEGY_CATCH_UP else None


def previous_report_day(day) -> date:
    return day - timedelta(days=3 if day.weekday() == 3 else 4)


def next_report_day(day) -> date:
    return day + timedelta(days=3 if day.weekday() == 0 else 4)


def compact(metrics):
    """What a reader needs from a snapshot; the 100-point curve and file details stay in the database."""
    if not metrics:
        return None
    keep = {k: metrics.get(k) for k in (*BASELINE_KEYS, "avg_view_s", "likes", "comments", "reach_days")}
    keep["traffic"] = (metrics.get("traffic") or [])[:4]
    keep["search_terms"] = (metrics.get("search_terms") or [])[:5]
    keep["ctr_by_source"] = (metrics.get("ctr_by_source") or [])[:4]
    drops = metrics.get("scene_drops") or []
    for name, order in (("steepest_scenes", -1), ("gentlest_scenes", 1)):
        keep[name] = [{k: d[k] for k in ("heading", "type", "start", "end", "per_minute")}
                      for d in sorted(drops, key=lambda d, order=order: order * d["per_minute"])[:3]]
    return keep


def plan_summary(job):
    plan, artifacts = (job or {}).get("plan") or {}, (job or {}).get("artifacts") or {}
    scenes = plan.get("scenes") or []
    upload = plan.get("upload") or {}
    return {"thumbnail_text": " / ".join(x for x in (plan.get("thumbnail"), plan.get("thumbnail_stat")) if x) or None,
            "cold_open": scenes[0].get("narration") if scenes else None,
            "lead": upload.get("lead") or plan.get("introduction"), "stories": upload.get("stories"),
            "scenes": [{k: s.get(k) for k in ("type", "heading", "t0", "t1")} for s in artifacts.get("scenes") or []]
            or [{"chapter": c["title"], "start": c["start"]} for c in artifacts.get("chapters") or []]}


def shorts_summary(plan):
    """What a reader needs from a Shorts cut (ShortsPlan JSON): its hook and the issues it lists."""
    plan = plan or {}
    hook = plan.get("hook") or {}
    return {"hook": hook.get("headline"), "question": hook.get("question"), "hook_narration": hook.get("narration"),
            "issues": [i.get("label") for i in plan.get("issues") or []]}


def report_video(row, diagnosis):
    """One measured upload as the strategy report reads it: its three-day figures (and the latest when later), how it
    was packaged (title hook, thumbnail text and cold open, or a Shorts' hook) and what it was made with."""
    video = {"youtube_id": row["youtube_id"], "title": row["title"], "hook": title_hook(row["title"]),
             "edition": EDITIONS.get(row["edition_kind"], "기타"),
             "published_kst": row["published_at"].astimezone(KST).isoformat(), "duration_s": row["duration_s"],
             "d3": compact(row["d3"]), "diagnosis": diagnosis, "craft": row["craft"], "lessons": row["lessons"]}
    if row["horizon"] != "d3":
        video["latest"] = {"horizon": row["horizon"], **compact(row["metrics"])}
    if row["edition_kind"] == "shorts":
        video["shorts"] = shorts_summary(row["plan"])
    else:
        summary = plan_summary(row)
        video["thumbnail_text"], video["cold_open"] = summary["thumbnail_text"], (summary["cold_open"] or "")[:200]
    return video


def usable_changes(publication, changes):
    """Studio edits the owner can paste as given. A new title must still read as this upload's own edition (the same
    day and kind as its current title, else its kind), so the upload stays linked and measured after the change."""
    current = parse_title(publication["title"], publication["published_at"])
    keep = []
    for change in changes:
        if change["target"] == "title":
            parsed = parse_title(change["text"], publication["published_at"])
            if (parsed is None or change["text"].strip() == publication["title"].strip()
                    or (parsed != current if current else parsed[1] != publication["edition_kind"])):
                continue
        keep.append(change)
    return keep


class AnalyticsRunner:
    def __init__(self, company, client=None, provider=None):
        self.company, self.settings = company, company.settings
        self.store = AnalyticsStore(company)
        self._client, self._provider = client, provider

    @property
    def client(self):
        if self._client is None:
            from .analytics_youtube import AnalyticsClient

            self._client = AnalyticsClient(self.settings)
        return self._client

    @property
    def provider(self):
        if self._provider is None:
            self._provider = RuntimeClient(self.settings.video_model_runtime_url,
                                           self.settings.model_runtime_token.get_secret_value(),
                                           timeout_seconds=MODEL_WAIT_SECONDS, expected_provider="claude")
        return self._provider

    @property
    def thumbnails(self):
        return self.settings.video_artifact_dir / "analytics" / "thumbnails"

    async def tick(self, at=None):
        if not self.store.enabled():
            return {"state": "disabled"}
        at = at or utcnow()
        result = {"state": "ok"}
        for name, stage in (("uploads", self.sync_uploads), ("reports", self.sync_reports),
                            ("snapshots", lambda: self.take_snapshots(at)), ("cards", self.cards),
                            ("details", self.details), ("updates", self.updates), ("weekly", lambda: self.weekly(at))):
            try:
                result[name] = await stage()
            except (httpx.HTTPError, ValueError, OSError, PolicyError, ProviderFault) as error:
                # A bounded code only: exception text can carry signed report URLs.
                LOG.warning("video analytics %s failed: %s", name, type(error).__name__)
                result[name] = "failed:" + type(error).__name__
        return result

    # ---- Collection ------------------------------------------------------------------------------------------

    async def sync_uploads(self):
        return await asyncio.to_thread(self.store.upsert_publications, await self.client.uploads())

    async def sync_reports(self):
        listed, applied = await self.client.report_jobs(), 0
        for report_type in REPORT_TYPES:
            job = listed.get(report_type) or await asyncio.to_thread(self.store.report_job, report_type)
            if not job:
                created = await self.client.create_job(report_type)
                job, receipt = created["id"], created
            else:
                receipt = {"id": job, "listed": True}
            await asyncio.to_thread(self.store.save_report_job, report_type, job, receipt)
            known = await asyncio.to_thread(self.store.known_reports)
            for report in await self.client.reports(job):
                if report["id"] in known:
                    continue
                text = await self.client.download(report["url"])
                try:
                    applied += await asyncio.to_thread(self.store.record_report, report_type, report["id"],
                                                       report["day"], report["created"], text)
                except ValueError:
                    # An unexpected report shape is left unrecorded and logged; the other days still apply.
                    LOG.warning("video analytics report %s of %s unreadable", report["id"], report_type)
        return applied

    async def take_snapshots(self, at):
        today, taken = at.astimezone(PT).date(), 0
        for publication, horizon in (await asyncio.to_thread(self.store.due_snapshots, at))[:SNAPSHOTS_PER_TICK]:
            start, end = window(publication["published_at"], horizon)
            try:
                api = await self.client.video_metrics(publication["youtube_id"], start, end)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {400, 404}:
                    raise  # auth, quota and server errors stop the stage until the next tick
                LOG.warning("video analytics %s %s unreadable (%s)", publication["youtube_id"], horizon,
                            error.response.status_code)
                continue
            reach = await asyncio.to_thread(self.store.reach, publication["youtube_id"], start, end)
            if publication["edition_kind"] == "shorts":
                # Measured on the cut's own scenes; its manifest names no heading, so the scene type stands in.
                cut = ((await asyncio.to_thread(self.store.shorts, publication["job_id"])) or {}).get("artifacts") or {}
                artifacts = {**cut, "scenes": [{**s, "heading": s.get("heading") or s["type"]}
                                               for s in cut.get("scenes") or [] if "t0" in s and "t1" in s]}
            else:
                job = await asyncio.to_thread(self.store.job, publication["job_id"])
                artifacts = (job or {}).get("artifacts") or {}
            duration = publication["duration_s"] or artifacts.get("duration")
            curve = api["curve"]
            drops = []
            if curve and duration and (artifacts.get("scenes") or artifacts.get("chapters")):
                drops = (scene_drops(curve, duration, scenes=artifacts["scenes"]) if artifacts.get("scenes")
                         else scene_drops(curve, duration, chapters=artifacts["chapters"]))
            metrics = {**api["totals"], "impressions": reach["impressions"] if reach["days"] else None,
                       "ctr": reach["ctr"] if reach["days"] else None, "reach_days": reach["days"],
                       "ctr_by_source": reach["sources"], "hook_30s": hook_retention(curve, duration),
                       "relative_retention": api["relative_retention"], "traffic": api["traffic"],
                       "search_terms": api["search_terms"], "scene_drops": drops, "curve": curve,
                       "report": today <= end + timedelta(days=REPORT_DAYS),
                       "thumbnail": await self.keep_thumbnail(publication)}
            await asyncio.to_thread(self.store.save_snapshot, publication["youtube_id"], horizon, start, end, metrics,
                                    taken_at=at)
            taken += 1
        return taken

    async def keep_thumbnail(self, publication):
        """The thumbnail viewers saw, kept on the video volume so a re-asked analysis sends the identical image."""
        try:
            content, kind = await self.client.thumbnail(publication["thumbnail_url"])
        except (httpx.HTTPError, ValueError):
            return None
        digest = hashlib.sha256(content).hexdigest()
        name = f"{publication['youtube_id']}-{digest[:16]}." + ("png" if kind == "image/png" else "jpg")
        self.thumbnails.mkdir(parents=True, exist_ok=True)
        path = self.thumbnails / name
        if not path.exists():
            await asyncio.to_thread(path.write_bytes, content)
        return {"file": name, "sha256": digest, "media_type": kind}

    @staticmethod
    def kept(youtube_id, metrics):
        """Reference to the stored thumbnail of a snapshot (file name and digest), or None."""
        kept = (metrics or {}).get("thumbnail")
        return {"youtube_id": youtube_id, **kept} if kept else None

    def attachment(self, ref):
        path = self.thumbnails / ref["file"]
        try:
            content = path.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(content).hexdigest() != ref["sha256"]:
            return None
        return ProviderAttachment(name=f"thumb-{ref['youtube_id']}." + path.suffix.lstrip("."),
                                  media_type=ref["media_type"], data=base64.b64encode(content).decode())

    # ---- Analysis --------------------------------------------------------------------------------------------

    async def analyze(self, request_id, kind, build, cls, contract, **ids):
        """(receipt, frozen request). The request (prompt, thumbnail references, model, effort and `extra` for the
        post) is built once by `build()` and frozen with the analysis row: asked again while claude-runtime still
        runs it, the identical request goes out however the inputs moved on. The receipt is {'output': …} or
        {'code': …} of a finished turn, or None while it is to be asked again (busy, quota, pending)."""
        row = await asyncio.to_thread(self.store.analysis, request_id)
        frozen = row["request"] if row and row["request"] else await asyncio.to_thread(build)
        images = [self.attachment(ref) for ref in frozen["attachments"]]
        done = await asyncio.to_thread(self.store.begin_analysis, request_id, kind, frozen, **ids)
        if done is not None:
            return done, frozen
        if any(image is None for image in images):
            receipt = {"code": "thumbnail_missing", "detail": "a frozen thumbnail file is gone"}
            await asyncio.to_thread(self.store.fail_analysis, request_id, receipt)
            return receipt, frozen
        request = ProviderRequest(request_id=request_id, model=frozen["model"], reasoning_effort=frozen["effort"],
                                  prompt=frozen["prompt"], output_contract=contract, attachments=images)
        try:
            response = await self.provider.run(request)
        except ProviderFault as error:
            if error.code in {"busy", "quota"}:
                await asyncio.to_thread(self.store.reject_analysis, request_id, error.code)
                return None, frozen
            if error.code in {"pending", "unavailable"}:
                return None, frozen
            if error.code in {"invalid_output", "timeout", "uncertain"}:
                receipt = {"code": error.code, "detail": str(error.message)[:500]}
                await asyncio.to_thread(self.store.fail_analysis, request_id, receipt)
                return receipt, frozen
            raise
        decision = response.decision
        if (response.provider != "claude" or response.usage.get("actual_model") != request.model
                or response.request_id != request.request_id or len(decision.artifacts) != 1
                or decision.status != "complete" or decision.tools or decision.delegations or decision.messages
                or decision.memories or decision.follow_up or response.web_searches):
            raise ValueError("Video analytics response identity mismatch")
        try:
            output = cls.model_validate_json(decision.artifacts[0].content)
        except ValidationError as error:
            # Locations and rules only, never the rejected text (as claude-runtime reports them): a report's retry
            # hands them back to the model.
            receipt = {"code": "invalid_output", "detail": ("output contract: " + "; ".join(
                ".".join(map(str, e["loc"])) + ": " + e["msg"]
                for e in error.errors(include_url=False, include_input=False)))[:500]}
            await asyncio.to_thread(self.store.fail_analysis, request_id, receipt)
            return receipt, frozen
        receipt = {"output": output.model_dump(mode="json"), "usage": response.usage, "provider": response.provider,
                   "request_id": response.request_id}
        await asyncio.to_thread(self.store.finish_analysis, request_id, receipt)
        return receipt, frozen

    def frozen(self, prompt, refs, effort, **extra):
        return {"model": self.settings.video_model, "effort": effort, "prompt": prompt,
                "attachments": [r for r in refs if r], "extra": extra}

    async def cards(self):
        pending = await asyncio.to_thread(self.store.unposted, "d3")
        if not pending or await asyncio.to_thread(self.store.producing):
            return 0
        posted = 0
        for row in pending:
            youtube_id = row["youtube_id"]
            ref = await asyncio.to_thread(self.store.baselines, row, "d3", tuple(OUTLIER), OUTLIER_MIN)
            reasons = outlier(row["metrics"], ref)
            if not reasons:
                continue  # Close to its own kind: no diagnosis, no card; the strategy report covers it.

            def build(row=row, youtube_id=youtube_id, reasons=reasons, compared=ref["n"]):
                base = self.store.baselines(row, "d3", BASELINE_KEYS)
                summary = (shorts_summary((self.store.shorts(row["job_id"]) or {}).get("plan"))
                           if row["edition_kind"] == "shorts" else plan_summary(self.store.job(row["job_id"])))
                context = {"video": {"youtube_id": youtube_id, "title": row["title"],
                                     "proposed_title": row["proposed_title"],
                                     "edition": EDITIONS.get(row["edition_kind"], "기타"),
                                     "published_kst": row["published_at"].astimezone(KST).isoformat(),
                                     "duration_s": row["duration_s"], **summary},
                           "window": {"horizon": "d3", "start": row["window_start"], "end": row["window_end"]},
                           "metrics": {k: v for k, v in row["metrics"].items()
                                       if k not in {"curve", "thumbnail", "report"}},
                           "baselines": base, "outlier": {"reasons": reasons, "compared_with": compared}}
                return self.frozen(performance_prompt(context), [self.kept(youtube_id, row["metrics"])],
                                   self.settings.video_reasoning_effort, baselines=base, outlier=reasons,
                                   outlier_n=compared)

            request_id = f"video-analytics-{youtube_id}-d3"
            try:
                receipt, frozen = await self.analyze(request_id, "video", build, VideoPerformance,
                                                     "video_performance_v1", youtube_id=youtube_id)
            except PolicyError as error:
                # The company's daily model allowance; the next tick asks again.
                LOG.warning("video analytics card %s refused: %s", youtube_id, error)
                continue
            if receipt is None:
                continue
            if receipt.get("output"):
                receipt = {**receipt, "output": {**receipt["output"],
                                                 "changes": usable_changes(row, receipt["output"]["changes"])}}
            # The card shows the baselines and outlying metrics the diagnosis was given (none in a request frozen
            # before cards were limited to outliers).
            extra = frozen["extra"]
            await asyncio.to_thread(self.store.post, youtube_id, f"{youtube_id}:d3", f"영상 성과 {youtube_id}",
                                    card_text(row, extra["baselines"], receipt, extra.get("outlier", ()),
                                              extra.get("outlier_n", 0)), analysis_request=request_id)
            posted += 1
        return posted

    async def details(self):
        """The full diagnosis as the first reply under each card, once Slack has confirmed the card."""
        posted = 0
        for row in await asyncio.to_thread(self.store.undetailed):
            youtube_id = row["youtube_id"]
            if await asyncio.to_thread(self.store.post, youtube_id, f"{youtube_id}:d3:detail", f"영상 성과 {youtube_id}",
                                       detail_text(row, row["baselines"], row["output"]), reply_to=f"{youtube_id}:d3"):
                posted += 1
        return posted

    async def updates(self):
        posted = 0
        for row in await asyncio.to_thread(self.store.unposted, "d7"):
            youtube_id = row["youtube_id"]
            if not await asyncio.to_thread(self.store.posted, f"{youtube_id}:d3"):
                continue  # No card (a backfilled upload or not an outlier): the strategy report covers it.
            base = await asyncio.to_thread(self.store.baselines, row, "d7", BASELINE_KEYS)
            if await asyncio.to_thread(self.store.post, youtube_id, f"{youtube_id}:d7", f"영상 성과 {youtube_id}",
                                       update_text(row, base), reply_to=f"{youtube_id}:d3"):
                posted += 1
        return posted

    async def weekly(self, at):
        """The channel strategy report (kind 'weekly', post weekly:<day>) on Monday and Thursday mornings over every
        measured upload, the ones measured since the previous report first. An answer failing its output contract is
        asked once more (-r2) with the contract errors; failing again, the numbers go out with the failure code."""
        day = report_day(at)
        if day is None:
            return 0
        if await asyncio.to_thread(self.store.posted, f"weekly:{day}"):
            return await self.report_detail(day)
        start = report_start(day)
        rows = [e for e in await asyncio.to_thread(self.store.episodes) if e["metrics"] and e["published_at"] < start]
        if not rows or await asyncio.to_thread(self.store.producing):
            return 0
        previous = report_start(previous_report_day(day))
        stats = report_stats(rows, start, previous, report_start(previous_report_day(previous_report_day(day))))
        active = await asyncio.to_thread(self.store.lessons, ["active"])
        unlinked = sum(e["job_id"] is None for e in rows)

        def build():
            diagnoses = {a["youtube_id"]: a["output"]["summary"]
                         for a in self.store.analyses("video", rows[0]["published_at"])}
            order = stats["new"] + [e["youtube_id"] for e in reversed(rows) if e["youtube_id"] not in stats["new"]]
            by_id = {e["youtube_id"]: e for e in rows}
            refs = [r for r in (self.kept(i, by_id[i]["metrics"]) for i in order) if r][:MAX_THUMBNAILS]
            lessons = []
            for lesson in active:
                adopted = lesson["decided_at"] or lesson["created_at"]
                with_it = [e for e in rows if str(lesson["id"]) in e["lessons"]]
                before = [e for e in rows if e["published_at"] < adopted
                          and e["edition_kind"] in {x["edition_kind"] for x in with_it}]
                lessons.append({"id": str(lesson["id"]), "scope": lesson["scope"], "text": lesson["text"],
                                "adopted": adopted, "with": medians([{"metrics": e["d3"]} for e in with_it]),
                                "before": medians([{"metrics": e["d3"]} for e in before])})
            context = {"report_day": day, "data_day": stats["data_day"], "period": {"from": previous, "to": start},
                       "new_videos": stats["new"],
                       "figures": {k: v for k, v in stats.items() if k not in {"data_day", "new"}},
                       "active_lessons": lessons, "unlinked_uploads": unlinked,
                       "thumbnail_attachments": [r["youtube_id"] for r in refs], "benchmark": None,
                       "videos": [report_video(by_id[i], diagnoses.get(i)) for i in order]}
            prompt = strategy_prompt(context)
            while len(prompt) > PROMPT_LIMIT and len(context["videos"]) > 1:
                context["videos"] = context["videos"][:-1]  # the oldest of the earlier videos first
                prompt = strategy_prompt(context)
            return self.frozen(prompt, refs, self.settings.video_review_effort, stats=as_json(stats), benchmark=None,
                               unlinked=unlinked)

        request_id = f"video-analytics-weekly-{day}"
        receipt, frozen = await self.analyze(request_id, "weekly", build, StrategyReport, "video_weekly_v1", week=day)
        if receipt and receipt.get("code") == "invalid_output":
            # The frozen first request with its stored contract errors appended (same model, effort and thumbnails);
            # a re-ask of the retry reads its own frozen row back.
            request_id, first = request_id + "-r2", frozen
            note = RETRY_NOTE.format(detail=(receipt.get("detail") or "")[:500])
            receipt, frozen = await self.analyze(request_id, "weekly", lambda: {**first, "prompt": first["prompt"] + note},
                                                 StrategyReport, "video_weekly_v1", week=day)
        if receipt is None:
            return 0
        output = receipt.get("output")
        if not output:
            text = failed_report_text(day, receipt.get("code", "unknown"), frozen["extra"])
        else:
            known = {p["youtube_id"] for p in await asyncio.to_thread(self.store.publications)}
            texts = {x["text"] for x in active}
            proposals = [p for p in output["strategies"] if set(p["videos"]) <= known and p["text"] not in texts]
            current = {str(x["id"]): x["text"] for x in active}
            verdicts = [{**v, "text": current[v["lesson_id"]]} for v in output["verdicts"] if v["lesson_id"] in current]
            analysis = await asyncio.to_thread(self.store.analysis, request_id)

            def propose():
                with self.store.db.transaction() as conn:
                    return self.store.propose(conn, analysis["id"], proposals)

            await asyncio.to_thread(propose)
            text, _ = report_texts(day, output, frozen["extra"], verdicts, len(proposals))
        await asyncio.to_thread(self.store.post, f"weekly:{day}", f"weekly:{day}", f"채널 전략 리포트 {day}", text,
                                analysis_request=request_id)
        return 1

    async def report_detail(self, day):
        """The details of a report too long for one post, as the first reply under it once Slack confirmed it; nothing
        when the report failed, the root already holds them, or the posted review predates the strategy report."""
        report = await asyncio.to_thread(self.store.report_analysis, day)
        output = (report or {}).get("output")
        if not output or "headline" not in output or await asyncio.to_thread(self.store.posted, f"weekly:{day}:detail"):
            return 0
        detail = report_detail_text(output, report["request"]["extra"])
        root = await asyncio.to_thread(self.store.posted, f"weekly:{day}")
        if not detail or detail in root["text"]:
            return 0
        return int(bool(await asyncio.to_thread(self.store.post, f"weekly:{day}", f"weekly:{day}:detail",
                                                f"채널 전략 리포트 {day}", detail, reply_to=f"weekly:{day}")))

    # ---- The owner's questions in analytics threads -----------------------------------------------------------

    @staticmethod
    def thread_of(question):
        """(project key, root post key, project title) of the thread a question was asked in."""
        if question["youtube_id"]:
            return question["youtube_id"], f"{question['youtube_id']}:d3", f"영상 성과 {question['youtube_id']}"
        return question["thread_key"], question["thread_key"], f"채널 전략 리포트 {question['week']}"

    def answer_context(self, question):
        """What the answer is built on: the thread's report and its inputs, the earlier exchange, the new message.
        The images are the thumbnails the report itself was given."""
        history = [{"owner": h["question"], "answer": h["answer"]} for h in self.store.thread_history(question)]
        if question["youtube_id"]:
            youtube_id = question["youtube_id"]
            report = self.store.analysis(f"video-analytics-{youtube_id}-d3")
            publication = self.store.publication(youtube_id)
            snapshots = {h: self.store.snapshot(youtube_id, h) for h in ("d3", "d7", "d28")}
            thread = {"video": {"youtube_id": youtube_id, "title": publication["title"],
                                "proposed_title": publication["proposed_title"],
                                "edition": EDITIONS.get(publication["edition_kind"], "기타"),
                                "published_kst": publication["published_at"].astimezone(KST).isoformat(),
                                "duration_s": publication["duration_s"],
                                **plan_summary(self.store.job(publication["job_id"]))},
                      "metrics": {h: compact(snap["metrics"]) for h, snap in snapshots.items() if snap},
                      "baselines": ((report or {}).get("request") or {}).get("extra", {}).get("baselines"),
                      "report": (report or {}).get("output")}
        else:
            report = self.store.report_analysis(question["week"])
            start = report_start(question["week"])
            videos = [{"youtube_id": e["youtube_id"], "title": e["title"], "edition": EDITIONS.get(e["edition_kind"], "기타"),
                       "horizon": e["horizon"], "metrics": compact(e["metrics"])}
                      for e in self.store.episodes() if e["metrics"] and e["published_at"] < start][-THREAD_VIDEOS:]
            thread = {"strategy_report": (report or {}).get("output"), "report_day": question["week"],
                      "figures": ((report or {}).get("request") or {}).get("extra", {}).get("stats"), "videos": videos}
        refs = ((report or {}).get("request") or {}).get("attachments") or []
        return {"thread": thread, "earlier": history, "owner_message": question["text"]}, refs

    async def answer(self, question_id):
        """Answer one question in its thread: 'answered', 'closed' (answered or given up already), 'waiting' (an
        episode is in production, claude-runtime is busy or today's allowance is used: ask again later) or
        'disabled'. While an episode is made the owner is told once that the answer follows."""
        question = await asyncio.to_thread(self.store.question, question_id)
        if not question or question["state"] != "pending":
            return "closed"
        if not self.store.enabled():
            return "disabled"
        key, root, title = self.thread_of(question)
        if await asyncio.to_thread(self.store.producing):
            if await asyncio.to_thread(self.store.notice_waiting, question_id):
                await asyncio.to_thread(self.store.post, key, f"question:{question_id}:wait", title, WAIT_TEXT,
                                        reply_to=root)
            return "waiting"

        def build():
            context, refs = self.answer_context(question)
            prompt = answer_prompt(context)
            while len(prompt) > PROMPT_LIMIT and context["thread"].get("videos"):
                context["thread"]["videos"] = context["thread"]["videos"][1:]  # oldest first
                prompt = answer_prompt(context)
            return self.frozen(prompt, refs, self.settings.video_reasoning_effort)

        request_id = f"video-analytics-answer-{question_id}"
        try:
            receipt, _ = await self.analyze(request_id, "answer", build, VideoAnswer, "video_answer_v1",
                                            youtube_id=question["youtube_id"], week=question["week"])
        except PolicyError as error:
            LOG.warning("video analytics answer %s refused: %s", question_id, error)
            return "waiting"
        if receipt is None:
            return "waiting"
        output = receipt.get("output")
        if output:
            publication = (await asyncio.to_thread(self.store.publication, question["youtube_id"])
                           if question["youtube_id"] else None)
            changes = usable_changes(publication, output["changes"]) if publication else []
            lesson = output.get("lesson")
            if lesson:
                known = {p["youtube_id"] for p in await asyncio.to_thread(self.store.publications)}
                active = {x["text"] for x in await asyncio.to_thread(self.store.active_lessons)}
                if not set(lesson["videos"]) <= known or lesson["text"] in active:
                    lesson = None
            if lesson:
                analysis = await asyncio.to_thread(self.store.analysis, request_id)

                def propose():
                    with self.store.db.transaction() as conn:
                        return self.store.propose(conn, analysis["id"], [lesson])

                await asyncio.to_thread(propose)
            text = answer_text(output, changes, lesson)
        else:
            text = failed_answer_text(receipt.get("code", "unknown"))
        if not await asyncio.to_thread(self.store.post, key, f"answer:{question_id}", title, text, reply_to=root,
                                       analysis_request=request_id):
            return "waiting"
        await asyncio.to_thread(self.store.close_question, question_id, "answered" if output else "failed")
        return "answered"

    async def give_up(self, question_id):
        """After hours of waiting: say so in the thread and close the question."""
        question = await asyncio.to_thread(self.store.question, question_id)
        if not question or question["state"] != "pending":
            return "closed"
        key, root, title = self.thread_of(question)
        await asyncio.to_thread(self.store.post, key, f"question:{question_id}:expired", title, EXPIRED_TEXT,
                                reply_to=root)
        await asyncio.to_thread(self.store.close_question, question_id, "expired")
        return "expired"

    @activity.defn(name="company_video_analytics_answer")
    async def activity_answer(self, question_id: str) -> str:
        return await self.answer(question_id)

    @activity.defn(name="company_video_analytics_give_up")
    async def activity_give_up(self, question_id: str) -> str:
        return await self.give_up(question_id)

    async def preview(self, at=None):
        """sync --dry-run: the YouTube side only (uploads, reporting jobs, the latest finished d3 window) so the
        numbers can be compared with Studio before anything is stored, posted or analysed."""
        at = at or utcnow()
        uploads = await self.client.uploads()
        rows = []
        for item in uploads[:10]:
            parsed = parse_title(item["title"], item["published_at"])
            rows.append({"youtube_id": item["youtube_id"], "title": item["title"],
                         "published_kst": item["published_at"].astimezone(KST).isoformat(),
                         "series": in_series(item["title"]),
                         "edition": parsed[1] if parsed else None, "day": parsed[0].isoformat() if parsed else None})
        latest = None
        today = at.astimezone(PT).date()
        for item in uploads:
            if not in_series(item["title"]):
                continue
            start, end = window(item["published_at"], "d3")
            if end < today:
                latest = {"youtube_id": item["youtube_id"], "start": start.isoformat(), "end": end.isoformat(),
                          "metrics": await self.client.video_metrics(item["youtube_id"], start, end)}
                break
        return {"uploads": rows, "report_jobs": await self.client.report_jobs(), "latest_window": latest}

    @activity.defn(name="company_video_analytics_tick")
    async def activity_tick(self):
        return await self.tick()
