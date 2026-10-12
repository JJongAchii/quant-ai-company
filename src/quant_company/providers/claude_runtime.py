"""The same private, authenticated receipt transport; separate Claude credentials and process."""

from .claude_runner import runner_from_environment
from .codex_runtime import create_app

# Visual reviews carry about 16 compressed 960x540 frames.
app = create_app(runner_factory=runner_from_environment, max_request_bytes=16 * 1024 * 1024)
