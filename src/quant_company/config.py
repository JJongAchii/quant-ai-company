from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql://localhost/quant_company"
    roles_file: Path | None = None
    operator_token: SecretStr = SecretStr("")
    model_provider: str = "codex"
    model_runtime_url: str = "http://codex:8081"
    model_runtime_token: SecretStr = SecretStr("")
    model_accounts_enabled: bool = False
    model_assignments_enabled: bool = False
    model_accounts_owner_user: str = ""
    model_accounts_channel_id: str = ""
    company_lake_uri: str = Field(default="", pattern=r"^(|s3://[a-z0-9][a-z0-9.-]+/[A-Za-z0-9_/-]+)$")
    slack_team_id: str = ""
    slack_allowed_users: list[str] = Field(default_factory=list)
    slack_allowed_channels: list[str] = Field(default_factory=list)
    slack_credentials_file: Path | None = None
    company_improvements_enabled: bool = False
    improvements_channel_id: str = ""
    data_watch_enabled: bool = False
    data_watch_publish_enabled: bool = False
    data_watch_core_enabled: bool = False
    data_watch_channel_id: str = ""
    data_watch_owner_user: str = ""
    data_watch_contracts_file: Path | None = None
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_api_key: SecretStr = SecretStr("")
    temporal_tls: bool = False
    temporal_task_queue: str = "quant-company"
    # Zero disables our daily quota; provider quotas and bounded task execution remain.
    company_max_daily_turns: int = Field(default=0, ge=0, le=10000)
    company_code_commit: str = "unknown"
    company_research_enabled: bool = False
    company_autonomous_research_enabled: bool = False
    research_audit_max_turns: int = Field(default=64, ge=2, le=128)
    research_audit_max_uncached_tokens: int = Field(default=750000, ge=10000, le=2000000)
    research_audit_max_output_tokens: int = Field(default=100000, ge=1000, le=200000)
    # Operator-provisioned, immutable execution profiles. Never a model-selected path.
    research_profiles_file: Path | None = None
    research_data_evidence_file: Path | None = None
    research_qlab_profile_file: Path | None = None
    research_worker_token: SecretStr = SecretStr("")
    research_artifact_dir: Path = Path("/var/lib/quant-company/research")
    research_report_bucket: str = ""
    research_report_prefix: str = "company/research/executions"
    research_library_channel_id: str = ""
    research_s3_credentials_file: Path | None = None
    company_web_enabled: bool = True
    company_news_enabled: bool = False
    news_publish_enabled: bool = False
    news_search_enabled: bool = False
    news_optimization_enabled: bool = False
    news_delivery_window_enabled: bool = False
    news_channel_id: str = ""
    news_owner_user: str = ""
    news_sources_file: Path | None = None
    news_max_age_hours: int = Field(default=24, ge=1, le=72)
    news_initial_lookback_minutes: int = Field(default=120, ge=0, le=1440)
    briefing_enabled: bool = False
    briefing_publish_enabled: bool = False
    briefing_source_notes_enabled: bool = False
    briefing_max_revisions: int = Field(default=1, ge=0, le=1)
    briefing_evaluation_edition_id: str = Field(default='', pattern=r'^(?:|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$')
    briefing_evaluation_edition_ids: list[str] = Field(default_factory=list, max_length=12)
    briefing_search_enabled: bool = True
    briefing_channel_id: str = ""
    briefing_owner_user: str = ""
    video_enabled: bool = False
    video_upload_enabled: bool = False
    video_publish_enabled: bool = False
    video_artifact_dir: Path = Path("/var/lib/quant-company/video")
    video_credentials_dir: Path = Path("/run/secrets/video")
    video_youtube_channel_id: str = ""
    video_runway_workspace_id: int = Field(default=0, ge=0)
    video_monthly_credit_limit: int = Field(default=1500, ge=0, le=100000)
    video_episode_credit_limit: int = Field(default=100, ge=0, le=500)
    video_voice: str = "Vincent"
    # Script and review run on the isolated Claude subscription runtime, never the Codex lane.
    video_model: str = "claude-opus-5"
    video_model_runtime_url: str = "http://claude-runtime:8080"
    video_ffmpeg: str = "ffmpeg"
    video_ffprobe: str = "ffprobe"
    video_alignment_model: str = "small"
    # motion-v2: fixed card/motion template (docs/DAILY_BRIEF_DESIGN_SPEC.md); text-v1: the earlier text slides.
    video_template: Literal["motion-v2", "text-v1"] = "motion-v2"
    video_asset_dir: Path = Path("/var/lib/quant-company/video-assets")
    video_render_workers: int = Field(default=1, ge=1, le=8)
    # 증시story playlist; when empty the description omits the "▶ 증시story 모아보기" lines.
    video_playlist_url: str = "https://www.youtube.com/playlist?list=PLbCkACCer37U"
    # Morning review-ready target (HH:MM KST); public approval still expires at 09:00 KST.
    # YouTube publish times the owner targets (KST). Work starts the moment the brief body is delivered; the files
    # must be in the brief thread VIDEO_PUBLISH_LEAD_MINUTES earlier. Morning: 07:00 while New York is on daylight
    # time (US close 05:00 KST), 07:50 otherwise. Close: 18:00.
    video_am_publish_dst: str = Field(default="07:00", pattern=r"^0[6-8]:[0-5]\d$")
    video_am_publish_std: str = Field(default="07:50", pattern=r"^0[6-8]:[0-5]\d$")
    video_pm_publish: str = Field(default="18:00", pattern=r"^(1[6-9]|2[0-1]):[0-5]\d$")
    video_publish_lead_minutes: int = Field(default=10, ge=0, le=60)
    # Final files are kept this many days after a job ends; intermediates are removed right after packaging.
    video_retention_days: int = Field(default=14, ge=1, le=90)
    # New episodes do not start below this free space on the artifact volume.
    video_min_free_gb: float = Field(default=10, ge=1, le=500)
    briefing_calendar_overrides_file: Path | None = None
    # Public chart reads after the US close; off until the briefing data worker is qualified for it.
    briefing_us_close_enabled: bool = False
    briefing_us_close_stocks: list[str] = Field(default_factory=lambda: ["NVDA", "MU", "AAPL", "MSFT"],
                                                max_length=8)
    # PM edition from the qdata regular-session KRX close snapshot (krx_close_*); off until qualified.
    # Minutes are measured from the actual KRX close C of the session (calendar overrides included).
    briefing_kr_close_enabled: bool = False
    briefing_kr_close_start_minutes: int = Field(default=20, ge=0, le=240)
    briefing_kr_close_cutoff_minutes: int = Field(default=40, ge=1, le=300)
    briefing_kr_close_due_minutes: int = Field(default=115, ge=2, le=360)
    # Only the 15:30 regular-session close is supported; the after-market (16:00~20:00) basis is not.
    briefing_kr_close_basis: str = "regular"
    tech_feed_enabled: bool = False
    tech_feed_publish_enabled: bool = False
    tech_feed_channel_id: str = ""
    tech_feed_owner_user: str = ""
    tech_feed_sources_file: Path | None = None
    trend_feed_enabled: bool = False
    trend_feed_publish_enabled: bool = False
    trend_feed_channel_id: str = ""
    trend_feed_owner_user: str = ""
    trend_feed_naver_enabled: bool = False
    trend_feed_naver_credentials_file: Path | None = None
    trend_feed_publication_hours: list[int] = Field(default_factory=lambda: [8], min_length=1, max_length=24)
    trend_feed_on_demand_enabled: bool = False
    trend_feed_model_daily_limit: int = Field(default=2, ge=1, le=48)
    housing_feed_enabled: bool = False
    housing_feed_publish_enabled: bool = False
    housing_feed_channel_id: str = ""
    housing_feed_owner_user: str = ""
    housing_feed_allowed_channels: list[str] = Field(default_factory=list)
    housing_map_panel_enabled: bool = False
    quant_feed_enabled: bool = False
    quant_feed_publish_enabled: bool = False
    quant_feed_channel_id: str = ""
    quant_feed_owner_user: str = ""
    quant_feed_sources_file: Path | None = None
    company_staff_development_enabled: bool = False
    staff_daily_exercises: int = Field(default=2, ge=1, le=6)
    staff_max_calls_per_exercise: int = Field(default=3, ge=1, le=4)
    staff_schedule_hour_kst: int = Field(default=3, ge=0, le=23)
    company_staff_review_enabled: bool = False
    staff_review_runtime_url: str = "http://claude-runtime:8080"
    staff_review_daily_limit: int = Field(default=2, ge=1, le=12)
    company_max_task_turns: int = Field(default=8, ge=1, le=30)
    company_max_depth: int = Field(default=3, ge=0, le=5)
    # Zero disables the project-wide task quota; individual task and depth limits remain.
    company_max_project_tasks: int = Field(default=0, ge=0, le=500)
    company_model_timeout_seconds: int = Field(default=960, ge=10, le=1800)
    fixture_mode: bool = False

    @field_validator("briefing_calendar_overrides_file", mode="before")
    @classmethod
    def empty_briefing_overrides(cls, value):
        return None if value == "" else value

    @field_validator('briefing_evaluation_edition_ids')
    @classmethod
    def bounded_briefing_editions(cls, values):
        from uuid import UUID

        if len(set(values)) != len(values) or any(str(UUID(value)) != value for value in values):
            raise ValueError('Briefing evaluation IDs must be unique canonical UUIDs')
        return sorted(values)

    @field_validator('briefing_us_close_stocks')
    @classmethod
    def bounded_us_close_stocks(cls, values):
        import re

        if len(set(values)) != len(values) or any(not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', v) for v in values):
            raise ValueError('US close stocks must be unique plain ticker symbols')
        return values

    @field_validator('briefing_kr_close_basis')
    @classmethod
    def regular_session_close_basis(cls, value):
        if value != 'regular':
            raise ValueError("BRIEFING_KR_CLOSE_BASIS must be 'regular' (the 15:30 KRX regular-session close); "
                             f"{value!r} is not supported, including the after-market (16:00~20:00) basis")
        return value

    @model_validator(mode="after")
    def ordered_kr_close_window(self) -> "Settings":
        if not (self.briefing_kr_close_start_minutes < self.briefing_kr_close_cutoff_minutes
                < self.briefing_kr_close_due_minutes):
            raise ValueError("BRIEFING_KR_CLOSE_START_MINUTES < BRIEFING_KR_CLOSE_CUTOFF_MINUTES < "
                             "BRIEFING_KR_CLOSE_DUE_MINUTES is required (minutes after the KRX close)")
        return self

    @property
    def briefing_evaluation_ids(self):
        return ([self.briefing_evaluation_edition_id] if self.briefing_evaluation_edition_id
                else self.briefing_evaluation_edition_ids)

    @field_validator("trend_feed_publication_hours")
    @classmethod
    def trend_publication_hours(cls, hours):
        if any(hour < 0 or hour > 23 for hour in hours) or len(set(hours)) != len(hours):
            raise ValueError("Trend publication hours must be distinct hours from0 to23")
        return sorted(hours)

    @model_validator(mode="after")
    def explicit_simulation(self) -> "Settings":
        if self.model_assignments_enabled and not self.model_accounts_enabled:
            raise ValueError("Model assignments require owner-selected account control")
        if self.video_enabled and not (self.briefing_enabled and self.briefing_publish_enabled):
            raise ValueError("Video requires an enabled publishing briefing source")
        if self.video_enabled and self.video_model != "claude-opus-5":
            raise ValueError("Video scripts require the qualified Claude subscription model")
        if self.video_enabled and not self.video_runway_workspace_id:
            raise ValueError('Video production requires an explicit Runway workspace')
        if self.video_upload_enabled and not (self.video_enabled and self.video_youtube_channel_id):
            raise ValueError("Video uploads require production and an explicit YouTube channel")
        if self.video_publish_enabled and not self.video_upload_enabled:
            raise ValueError("Video public release requires private upload support")
        if self.model_accounts_enabled and self.model_accounts_owner_user not in self.slack_allowed_users:
            raise ValueError("Model account control requires an explicitly allowed owner")
        if self.model_accounts_enabled and (
            not self.model_accounts_channel_id.startswith("C")
            or self.model_accounts_channel_id not in self.slack_allowed_channels
            or self.model_accounts_channel_id in {self.improvements_channel_id, self.data_watch_channel_id,
                                                  self.news_channel_id}
        ):
            raise ValueError("Model account control requires a dedicated allowed Slack channel")
        if self.model_provider not in {"codex", "fixture"}:
            raise ValueError("MODEL_PROVIDER must be codex or fixture")
        if self.model_provider == "fixture" and not self.fixture_mode:
            raise ValueError("Fixture provider requires explicit FIXTURE_MODE=true")
        if self.company_autonomous_research_enabled and not self.company_research_enabled:
            raise ValueError("Autonomous research requires the durable research service")
        if self.company_improvements_enabled and (
            not self.improvements_channel_id.startswith("C")
            or self.improvements_channel_id not in self.slack_allowed_channels
        ):
            raise ValueError("Improvements requires an explicitly allowed Slack channel")
        if self.data_watch_enabled and (
            not self.data_watch_channel_id.startswith("C")
            or self.data_watch_channel_id not in self.slack_allowed_channels
            or self.data_watch_owner_user not in self.slack_allowed_users
            or self.data_watch_channel_id == self.improvements_channel_id
        ):
            raise ValueError("Data watch requires a dedicated allowed Slack channel and owner")
        if self.data_watch_core_enabled and not self.data_watch_enabled:
            raise ValueError("Data watch core checks require data watch")
        if self.briefing_evaluation_edition_ids and self.briefing_evaluation_edition_id:
            raise ValueError('Bounded briefing observation requires a single ID list')
        return self

    def require_operator_token(self) -> str:
        value = self.operator_token.get_secret_value()
        if len(value) < 24:
            raise ValueError("OPERATOR_TOKEN must contain at least 24 characters")
        return value
