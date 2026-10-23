"""deploy/prepare-state.py on a temporary state directory; ownership changes are skipped."""

import importlib.util
import os
import stat
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("prepare_state", Path(__file__).parents[1] / "deploy/prepare-state.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def apply(monkeypatch, state):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["prepare-state.py", "--state-dir", str(state), "--apply"])
    prepare.main()


def test_the_claude_api_key_placeholder_is_created_once_and_never_overwritten(tmp_path, monkeypatch):
    state = tmp_path / "state"
    apply(monkeypatch, state)
    key = state / "secrets/claude_api_key"
    assert key.read_text() == "" and stat.S_IMODE(key.stat().st_mode) == 0o444
    key.chmod(0o600)
    key.write_text("sk-ant-owner-key\n")
    apply(monkeypatch, state)
    assert key.read_text() == "sk-ant-owner-key\n" and stat.S_IMODE(key.stat().st_mode) == 0o600
