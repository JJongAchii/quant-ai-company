"""The same private, authenticated receipt transport; separate Claude credentials and process."""

from .claude_runner import runner_from_environment
from .codex_runtime import create_app

app = create_app(runner_factory=runner_from_environment)
