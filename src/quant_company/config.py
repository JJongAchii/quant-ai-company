from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql://localhost/quant_company"
    roles_file: Path | None = None
    operator_token: SecretStr = SecretStr("")
    model_provider: str = "codex"
    model_runtime_url: str = "http://codex:8081"
    model_runtime_token: SecretStr = SecretStr("")
    company_lake_uri: str = Field(default="", pattern=r"^(|s3://[a-z0-9][a-z0-9.-]+/[A-Za-z0-9_/-]+)$")
    slack_team_id: str = ""
    slack_allowed_users: list[str] = Field(default_factory=list)
    slack_allowed_channels: list[str] = Field(default_factory=list)
    slack_credentials_file: Path | None = None
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_api_key: SecretStr = SecretStr("")
    temporal_tls: bool = False
    temporal_task_queue: str = "quant-company"
    # Zero disables our daily quota; provider quotas and bounded task execution remain.
    company_max_daily_turns: int = Field(default=0, ge=0, le=10000)
    company_code_commit: str = "unknown"
    company_web_enabled: bool = True
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
    company_model_timeout_seconds: int = Field(default=360, ge=10, le=1800)
    fixture_mode: bool = False

    @model_validator(mode="after")
    def explicit_simulation(self) -> "Settings":
        if self.model_provider not in {"codex", "fixture"}:
            raise ValueError("MODEL_PROVIDER must be codex or fixture")
        if self.model_provider == "fixture" and not self.fixture_mode:
            raise ValueError("Fixture provider requires explicit FIXTURE_MODE=true")
        return self

    def require_operator_token(self) -> str:
        value = self.operator_token.get_secret_value()
        if len(value) < 24:
            raise ValueError("OPERATOR_TOKEN must contain at least 24 characters")
        return value
