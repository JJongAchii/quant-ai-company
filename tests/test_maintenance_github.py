import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from quant_company.maintenance.github import GitHub, GitHubError

from .test_maintenance import config


def client(handler):
    github = GitHub(config(), transport=httpx.MockTransport(handler))
    github._token, github._expires = "fixture-installation-token", time.time() + 3600
    return github


def job():
    return {"id": uuid4(), "created_at": datetime.now(UTC), "receipt": {
        "branch": "maintenance/fixture", "head": "c" * 40, "base": "a" * 40,
        "ci": {"state": "passed", "url": "https://github.com/example/ci"},
    }, "payload": {"snapshot": {"commit": "a" * 40, "tree": "b" * 40},
                    "changes": {"src/quant_company/tools.py": "fixture"},
                    "finding": {"title": "Fixture repair", "problem": "Observed problem",
                                "reproduction": "Reproduce with fixture", "expected": "Expected result",
                                "evidence_keys": ["message:fixture"]},
                    "summary": "Fixture patch", "patch_digest": "digest"}}


def test_publish_reconciles_lost_ref_response_without_force_push():
    writes, branch = [], {}
    def handler(request):
        path = request.url.path
        if request.method == "GET":
            if path.endswith("/heads/main"):
                return httpx.Response(200, json={"object": {"sha": "a" * 40}})
            return httpx.Response(200, json={"object": {"sha": branch["sha"]}}) if branch else httpx.Response(404)
        assert request.method == "POST", "No PATCH/PUT/DELETE or force push is permitted"
        body = json.loads(request.content)
        writes.append((path, body))
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "d" * 40})
        if path.endswith("/git/commits"):
            assert body["parents"] == ["a" * 40]
            return httpx.Response(201, json={"sha": "c" * 40})
        assert path.endswith("/git/refs") and body["ref"].startswith("refs/heads/maintenance/")
        branch["sha"] = body["sha"]
        raise httpx.ReadError("Lost response after server accepted the ref", request=request)
    github = client(handler)
    case = job()
    first = github.publish(case, case["payload"])
    again = github.publish(case, case["payload"])
    assert first == again
    assert sum(path.endswith("/git/refs") for path, _ in writes) == 1
    commits = [body for path, body in writes if path.endswith("/git/commits")]
    assert commits[0] == commits[1]


def test_moved_base_stops_before_any_git_write():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"object": {"sha": "e" * 40}})
    case = job()
    with pytest.raises(ValueError, match="base_moved"):
        client(handler).publish(case, case["payload"])


@pytest.mark.parametrize("test_conclusion", ["skipped", "failure", "success"])
def test_ci_binds_exact_head_workflow_attempt_and_real_test_step(test_conclusion):
    def handler(request):
        assert request.method == "GET"
        if request.url.path.endswith("quant-company-ci.yml"):
            return httpx.Response(200, json={"id": 10})
        if request.url.path.endswith("/runs"):
            assert request.url.params["head_sha"] == "c" * 40
            return httpx.Response(200, json={"workflow_runs": [{
                "id": 20, "run_attempt": 2, "workflow_id": 10, "head_sha": "c" * 40,
                "head_branch": "maintenance/fixture", "event": "push", "status": "completed",
                "conclusion": "success", "html_url": "https://github.com/example/run/20",
            }]})
        assert request.url.path.endswith("/runs/20/attempts/2/jobs")
        return httpx.Response(200, json={"jobs": [{"name": "service", "conclusion": "success", "steps": [
            {"name": "Install core dependencies", "conclusion": "success"},
            {"name": "Lint", "conclusion": "success"},
            {"name": "Regression reproduces on base", "conclusion": "success"},
            {"name": "PostgreSQL, Temporal and service regression tests", "conclusion": test_conclusion},
        ]}]})
    github = client(handler)
    if test_conclusion == "success":
        assert github.ci(job()["receipt"])["attempt"] == 2
    else:
        with pytest.raises(ValueError, match="steps_missing_or_skipped"):
            github.ci(job()["receipt"])


def test_ambiguous_pr_post_is_not_retried_automatically():
    posts = []
    def handler(request):
        if "/git/ref/heads/" in request.url.path:
            sha = "a" * 40 if request.url.path.endswith("/main") else "c" * 40
            return httpx.Response(200, json={"object": {"sha": sha}})
        if request.method == "GET":
            assert request.url.path.endswith("/pulls")
            return httpx.Response(200, json=[])
        posts.append(json.loads(request.content))
        raise httpx.ReadTimeout("Uncertain response", request=request)
    github = client(handler)
    github.ci = lambda receipt: {"state": "passed"}
    with pytest.raises(GitHubError, match="pr_write_requires_reconciliation"):
        github.pull_request(job())
    assert len(posts) == 1 and posts[0]["draft"] is True


def test_ci_recheck_blocks_new_failure_before_pr():
    def handler(request):
        assert request.method == "GET" and "/git/ref/heads/" in request.url.path
        sha = "a" * 40 if request.url.path.endswith("/main") else "c" * 40
        return httpx.Response(200, json={"object": {"sha": sha}})
    github = client(handler)
    github.ci = lambda receipt: {"state": "failed"}
    with pytest.raises(ValueError, match="ci_no_longer_passed"):
        github.pull_request(job())
