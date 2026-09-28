from pathlib import Path

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
    # Operator-provisioned, immutable execution profiles. Never a model-selected path.
    research_profiles_file: Path | None = None
    research_qlab_profile_file: Path | None = None
    research_worker_token: SecretStr = SecretStr("")
    research_artifact_dir: Path = Path("/var/lib/quant-company/research")
    research_report_bucket: str = ""
    research_report_prefix: str = "company/research/executions"
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
    briefing_search_enabled: bool = True
    briefing_channel_id: str = ""
    briefing_owner_user: str = ""
    briefing_calendar_overrides_file: Path | None = None
    tech_feed_enabled: bool = False
    tech_feed_publish_enabled: bool = False
    tech_feed_channel_id: str = ""
    tech_feed_owner_user: str = ""
    tech_feed_sources_file: Path | None = None
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
    company_max_project_tasks: int = Field(default=40, ge=1, le=500)
    company_model_timeout_seconds: int = Field(default=960, ge=10, le=1800)
    fixture_mode: bool = False

    @field_validator("briefing_calendar_overrides_file", mode="before")
    @classmethod
    def empty_briefing_overrides(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def explicit_simulation(self) -> "Settings":
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
        return self

    def require_operator_token(self) -> str:
        value = self.operator_token.get_secret_value()
        if len(value) < 24:
            raise ValueError("OPERATOR_TOKEN must contain at least 24 characters")
        return value
