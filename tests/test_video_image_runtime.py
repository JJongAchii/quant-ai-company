"""The separate video image runtime against a simulated Codex CLI: no subscription call, no network."""

import hashlib
import json
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from quant_company.contracts import ProviderFault
from quant_company.providers.codex_runner import SUPPORTED_CLI_VERSION, ProcessResult, RunnerConfig
from quant_company.providers.video_image_runtime import (
    IMAGE_FEATURES,
    ImageRequest,
    ImageRunner,
    create_app,
    image_command,
)

JOB = "video-02a27a7c-1929-580f-b444-160bca078dc1-illustration-gen-1"


def png(width=64, height=36):
    raw = b"".join(b"\x00" + b"\x10\x20\x40" * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FakeCodex:
    """Answers --version and login status; an exec turn writes the image the way the built-in tool does."""

    def __init__(self, home, *, image=True, items=("agent_message",), failed=None):
        self.home, self.image, self.items, self.failed, self.calls = home, image, items, failed, []

    async def run(self, argv, *, cwd, env, stdin=b"", timeout_seconds, max_stdout_bytes, max_stderr_bytes):
        self.calls.append({"argv": argv, "stdin": stdin, "env": env})
        if argv[1:] == ["--version"]:
            return ProcessResult(0, f"codex-cli {SUPPORTED_CLI_VERSION}\n".encode(), b"")
        if argv[1:3] == ["login", "status"]:
            return ProcessResult(0, b"Logged in using ChatGPT\n", b"")
        thread = "01a12f30-dc5c-7313-91a2-87b4df05f716"
        if self.image:
            folder = self.home / "generated_images" / thread
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "exec-1.png").write_bytes(png())
        if self.failed:
            events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.failed", "error": self.failed}]
            return ProcessResult(1, "\n".join(json.dumps(e) for e in events).encode(), b"")
        events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"}]
        events += [{"type": "item.completed", "item": {"id": f"i{n}", "type": kind, "text": "done"}}
                   for n, kind in enumerate(self.items)]
        events += [{"type": "turn.completed", "usage": {"input_tokens": 20177, "output_tokens": 184}}]
        return ProcessResult(0, "\n".join(json.dumps(e) for e in events).encode(), b"")


def runner(tmp_path, **fake):
    home = tmp_path / "auth"
    home.mkdir(parents=True)
    codex = FakeCodex(home, **fake)
    config = RunnerConfig(codex_home=home, jobs_dir=tmp_path / "jobs", timeout_seconds=300)
    return ImageRunner(config, process_runner=codex, environment={"PATH": "/usr/bin", "HOME": "/tmp"}), codex


def request(**extra):
    return ImageRequest(request_id=JOB, model="gpt-6-astra", prompt="A generic container ship at a port at dawn.", **extra)


async def test_one_request_id_generates_one_image_and_replays_the_receipt(tmp_path):
    images, codex = runner(tmp_path)
    first = await images.run(request())
    assert first["width"] == 64 and first["height"] == 36 and first["media_type"] == "image/png"
    assert first["sha256"] == hashlib.sha256((tmp_path / "jobs" / f"{JOB}.png").read_bytes()).hexdigest()
    assert not (tmp_path / "auth" / "generated_images" / "01a12f30-dc5c-7313-91a2-87b4df05f716").exists()
    execs = [c for c in codex.calls if c["argv"][1] == "exec"]
    assert len(execs) == 1 and (await images.run(request()))["sha256"] == first["sha256"]
    assert len([c for c in codex.calls if c["argv"][1] == "exec"]) == 1  # served from the receipt
    argv = execs[0]["argv"]
    assert "features.image_generation=true" in argv and "features.shell_tool=false" in argv
    assert "features.code_mode_host=true" in argv
    assert "features.unified_exec=false" in argv and 'web_search="disabled"' in argv and argv[-1] == "-"
    assert execs[0]["stdin"].startswith(b"Generate exactly one image") and "OPENAI_API_KEY" not in execs[0]["env"]
    with pytest.raises(ProviderFault) as reused:  # the same ID can never name a different image
        await images.run(request(reasoning_effort="high"))
    assert reused.value.code == "invalid_request"


async def test_turns_without_an_image_or_outside_image_generation_are_not_run_again(tmp_path):
    images, codex = runner(tmp_path, image=False)
    with pytest.raises(ProviderFault) as missing:
        await images.run(request())
    assert missing.value.code == "invalid_output"
    with pytest.raises(ProviderFault) as replay:
        await images.run(request())
    assert replay.value.code == "invalid_output" and len([c for c in codex.calls if c["argv"][1] == "exec"]) == 1
    tool, _ = runner(tmp_path / "tool", items=("agent_message", "command_execution"))
    with pytest.raises(ProviderFault) as escaped:
        await tool.run(request())
    assert escaped.value.code == "uncertain"


async def test_quota_is_definitive_and_an_interrupted_generation_is_uncertain(tmp_path):
    images, _ = runner(tmp_path, image=False, failed={"message": "usage_limit_reached"})
    with pytest.raises(ProviderFault) as quota:
        await images.run(request())
    assert quota.value.code == "quota"
    crashed, codex = runner(tmp_path / "crash")
    path = crashed.receipt_path(JOB)
    path.parent.mkdir(parents=True)
    from quant_company.providers.video_image_runtime import request_digest

    path.write_text(json.dumps({"request_id": JOB, "input_digest": request_digest(request()), "state": "running"}))
    with pytest.raises(ProviderFault) as uncertain:
        await crashed.run(request())
    assert uncertain.value.code == "uncertain" and not [c for c in codex.calls if c["argv"][1] == "exec"]


def test_the_image_command_enables_only_the_image_tool():
    from pathlib import Path

    from quant_company.providers.codex_runner import DISABLED_FEATURES

    argv = image_command(RunnerConfig(codex_home=Path("/state/auth"), jobs_dir=Path("/state/jobs")), request(),
                         Path("/state/jobs/.image-x"))
    enabled = {a for a in argv if a.startswith("features.") and a.endswith("=true")}
    # The image tool and the code-mode bridge that exposes it; everything else the shared runtime disables stays off.
    assert enabled == {"features.skip_host_skill_discovery=true", "features.image_generation=true",
                       "features.code_mode=true", "features.code_mode_host=true", "features.code_mode_only=true"}
    assert all(f"features.{name}=false" in argv for name in DISABLED_FEATURES if name not in IMAGE_FEATURES)
    assert "features.shell_tool=false" in argv and "features.apps=false" in argv and "features.multi_agent=false" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only" and "--ephemeral" in argv


def test_http_requires_the_runtime_token_and_serves_the_checked_image(tmp_path):
    images, _ = runner(tmp_path)
    client = TestClient(create_app(runner=images, token="secret"))
    body = {"request_id": JOB, "model": "gpt-6-astra", "reasoning_effort": "low",
            "prompt": "A generic container ship at a port at dawn."}
    assert client.post("/v1/images", json=body).status_code == 401
    made = client.post("/v1/images", json=body, headers={"Authorization": "Bearer secret"})
    assert made.status_code == 200 and made.json()["request_id"] == JOB
    got = client.get(f"/v1/images/{JOB}", headers={"Authorization": "Bearer secret"})
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == made.json()["sha256"]
    assert got.headers["x-image-sha256"] == made.json()["sha256"]
    assert client.post("/v1/images", json={**body, "request_id": "news-brief-x"},
                       headers={"Authorization": "Bearer secret"}).status_code == 422
