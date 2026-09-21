"""Privileged Git broker. It never executes candidate code or exposes credentials to a model."""

import base64
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .policy import SECRET, WORKFLOW, MaintenanceConfig, writable


def blob_sha(content):
    data = content.encode()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, config: MaintenanceConfig, *, transport=None):
        self.config, self.transport = config, transport
        self._token, self._expires = "", 0.0

    def _http(self, method, route, token, *, data=None, missing_ok=False):
        with httpx.Client(base_url="https://api.github.com", timeout=25, trust_env=False,
                          follow_redirects=False, transport=self.transport) as client:
            with client.stream(method, route, json=data, headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            }) as response:
                if missing_ok and response.status_code == 404:
                    return None
                if not 200 <= response.status_code < 300:
                    # Body can contain private details. The durable job records only a safe error code.
                    raise GitHubError(f"github_http_{response.status_code}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise GitHubError("github_response_too_large")
                return json.loads(body)

    def token(self):
        if time.time() < self._expires:
            return self._token
        def encoded(value):
            return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")
        at = int(time.time())
        unsigned = encoded({"alg": "RS256", "typ": "JWT"}) + b"." + encoded({
            "iat": at - 60, "exp": at + 540, "iss": str(self.config.app_id),
        })
        signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.config.private_key_file)],
                                input=unsigned, capture_output=True, timeout=10, check=False)
        if signed.returncode:
            raise GitHubError("github_app_signing_failed")
        jwt = (unsigned + b"." + base64.urlsafe_b64encode(signed.stdout).rstrip(b"=")).decode()
        permissions = {"contents": "write", "pull_requests": "write", "actions": "read"}
        data = self._http("POST", f"/app/installations/{self.config.installation_id}/access_tokens", jwt, data={
            "repositories": [self.config.repository.split("/")[1]], "permissions": permissions,
        })
        if any(permissions.get(k, "read" if k == "metadata" else None) != v
               for k, v in data["permissions"].items()):
            raise GitHubError("unexpected_installation_permissions")
        self._token = data["token"]
        self._expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp() - 60
        return self._token

    def request(self, method, path, **kwargs):
        return self._http(method, "/repos/" + self.config.repository + path, self.token(), **kwargs)

    def snapshot(self):
        ref = self.request("GET", "/git/ref/heads/" + quote(self.config.base, safe=""))
        commit = self.request("GET", "/git/commits/" + ref["object"]["sha"])
        tree = self.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
        if tree.get("truncated"):
            raise ValueError("repository_tree_truncated")
        entries = {item["path"]: item for item in tree["tree"]}
        if WORKFLOW not in entries or entries[WORKFLOW]["mode"] != "100644":
            raise ValueError("reviewed_ci_workflow_required_on_base")
        paths = [path for path, item in entries.items()
                 if writable(path) and item["type"] == "blob" and item["mode"] == "100644"]
        return {"commit": commit["sha"], "tree": commit["tree"]["sha"], "entries": entries, "paths": paths}

    def read_files(self, snapshot, paths):
        output = {}
        for path in paths:
            item = snapshot["entries"].get(path)
            if not item or item["type"] != "blob" or item["mode"] != "100644" or not writable(path):
                raise ValueError("protected_or_unknown_path")
            if item.get("size", 0) > 100000:
                raise ValueError("source_file_too_large")
            blob = self.request("GET", "/git/blobs/" + item["sha"])
            if blob["encoding"] != "base64":
                raise ValueError("unsupported_blob_encoding")
            content = base64.b64decode(blob["content"]).decode("utf-8")
            if blob_sha(content) != item["sha"]:
                raise ValueError("repository_blob_digest_mismatch")
            output[path] = content
        if sum(map(len, output.values())) > 300000:
            raise ValueError("source_context_too_large")
        return output

    def read_repository(self, snapshot, previous=None):
        """Read only allowlisted text blobs, reusing content-addressed evidence from the prior commit."""
        from ..system_state import readable

        previous = previous or {}
        output, omitted, total, reused, fetched = {}, [], 0, 0, 0
        for path, entry in sorted(snapshot["entries"].items()):
            if not readable(path):
                continue
            size = entry.get("size", 0)
            if (entry["type"] != "blob" or entry["mode"] != "100644" or size > 100000
                    or total + size > 2000000):
                omitted.append(path)
                continue
            content = previous.get(path)
            if content is not None and blob_sha(content) == entry["sha"]:
                reused += 1
            else:
                blob = self.request("GET", "/git/blobs/" + entry["sha"])
                if blob["encoding"] != "base64":
                    raise ValueError("unsupported_blob_encoding")
                content = base64.b64decode(blob["content"]).decode("utf-8")
                if blob_sha(content) != entry["sha"]:
                    raise ValueError("repository_blob_digest_mismatch")
                fetched += 1
            actual = len(content.encode())
            if actual > 100000 or total + actual > 2000000 or SECRET.search(content):
                omitted.append(path)
                continue
            output[path] = content
            total += actual
        return output, {"read_files": len(output), "read_bytes": total, "reused_files": reused,
                        "fetched_files": fetched, "omitted_paths": omitted,
                        "boundary": "allowlisted text blobs only; no credentials, binaries, links or arbitrary repository"}

    def current_metadata(self, snapshot):
        from ..system_state import readable

        prs = self.request("GET", "/pulls?state=all&sort=updated&direction=desc&per_page=15")
        runs = self.request("GET", "/actions/runs?per_page=15")["workflow_runs"]
        commit = self.request("GET", "/commits/" + snapshot["commit"] + "?per_page=30")
        changes = [{"path": f["filename"], "blob": f["sha"], "status": f["status"],
                    "patch_excerpt": f.get("patch", "")[:1200]}
                   for f in commit.get("files", []) if readable(f["filename"])
                   and not SECRET.search(f.get("patch", ""))][:8]
        return {"repository": self.config.repository, "branch": self.config.base,
                "pull_requests": [{"number": p["number"], "title": p["title"], "state": p["state"],
                                   "head": p["head"]["sha"], "merged_at": p.get("merged_at"),
                                   "merge_commit": p.get("merge_commit_sha"), "url": p["html_url"]} for p in prs],
                "ci": [{"head": r["head_sha"], "status": r["status"], "conclusion": r["conclusion"],
                        "event": r["event"], "url": r["html_url"]} for r in runs],
                "commit_changes": {"commit": snapshot["commit"], "parents": [p["sha"] for p in commit["parents"]],
                                   "files": changes, "coverage": "At most 8 safe patch excerpts from the first 30 changed files; not a complete diff."},
                "coverage_note": "Latest 15 PRs and CI runs (human and bot); absence from this window is unknown."}

    def publish(self, job, payload):
        branch = "maintenance/" + str(job["id"])
        if payload.get("patch_attempt", 1) > 1:
            branch += "-a" + str(payload["patch_attempt"])
        base = payload["snapshot"]
        current = self.request("GET", "/git/ref/heads/" + quote(self.config.base, safe=""))
        if current["object"]["sha"] != base["commit"]:
            raise ValueError("base_moved_review_and_rebuild_required")
        # Content-addressed objects plus a fixed date make retry after a lost response deterministic.
        tree = self.request("POST", "/git/trees", data={"base_tree": base["tree"], "tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in sorted(payload["changes"].items())
        ]})
        identity = {"name": "Quant Company Maintainer", "email": "maintainer@users.noreply.github.com",
                    "date": job["created_at"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        commit = self.request("POST", "/git/commits", data={
            "message": "fix: " + payload["finding"]["title"] + "\n\nMaintenance case: " + str(job["id"]),
            "tree": tree["sha"], "parents": [base["commit"]], "author": identity, "committer": identity,
        })
        ref_path = "/git/ref/heads/" + quote(branch, safe="")
        existing = self.request("GET", ref_path, missing_ok=True)
        if existing is None:
            try:
                self.request("POST", "/git/refs", data={"ref": "refs/heads/" + branch, "sha": commit["sha"]})
            except (GitHubError, httpx.HTTPError):
                # A lost create response may hide success. Read back, never update/force-push a ref.
                existing = self.request("GET", ref_path, missing_ok=True)
                if not existing or existing["object"]["sha"] != commit["sha"]:
                    raise GitHubError("branch_write_requires_reconciliation") from None
        elif existing["object"]["sha"] != commit["sha"]:
            raise ValueError("maintenance_branch_collision")
        return {"branch": branch, "head": commit["sha"], "base": base["commit"], "tree": tree["sha"]}

    def ci(self, receipt):
        workflow = self.request("GET", "/actions/workflows/" + WORKFLOW.split("/")[-1])
        runs = self.request("GET", f"/actions/workflows/{workflow['id']}/runs?event=push&per_page=100&head_sha="
                            + receipt["head"])["workflow_runs"]
        matching = [run for run in runs if run["head_sha"] == receipt["head"]
                    and run["head_branch"] == receipt["branch"] and run["event"] == "push"
                    and run["workflow_id"] == workflow["id"]]
        if not matching:
            return {"state": "pending"}
        run = max(matching, key=lambda r: (r["id"], r["run_attempt"]))
        if run["status"] != "completed":
            return {"state": "pending"}
        if run["conclusion"] != "success":
            return {"state": "failed", "run_id": run["id"], "attempt": run["run_attempt"], "url": run["html_url"]}
        jobs = self.request("GET", f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs")["jobs"]
        service = [job for job in jobs if job["name"] == "service"]
        if len(service) != 1 or service[0]["conclusion"] != "success":
            raise ValueError("expected_ci_job_not_successful")
        required = {"Install core dependencies", "Lint", "Regression reproduces on base",
                    "PostgreSQL, Temporal and service regression tests"}
        steps = {step["name"] for step in service[0]["steps"] if step["conclusion"] == "success"}
        if not required <= steps:
            raise ValueError("required_ci_steps_missing_or_skipped")
        return {"state": "passed", "run_id": run["id"], "attempt": run["run_attempt"], "url": run["html_url"]}

    def ci_failure(self, receipt):
        from .investigation import feedback_text

        ci = receipt["ci"]
        run = self.request("GET", f"/actions/runs/{ci['run_id']}")
        if run["head_sha"] != receipt["head"] or run["head_branch"] != receipt["branch"]:
            raise ValueError("ci_feedback_head_mismatch")
        jobs = self.request("GET", f"/actions/runs/{ci['run_id']}/attempts/{ci['attempt']}/jobs")["jobs"]
        service = [job for job in jobs if job["name"] == "service"]
        if len(service) != 1:
            raise ValueError("ci_feedback_service_job_missing")
        job = service[0]
        steps = [{"name": step["name"], "conclusion": step.get("conclusion")} for step in job["steps"]]
        path = f"https://api.github.com/repos/{self.config.repository}/actions/jobs/{job['id']}/logs"
        def read_log(stream):
            if stream.status_code != 200:
                raise GitHubError("ci_log_download_failed")
            tail, size = bytearray(), 0
            for chunk in stream.iter_bytes():
                size += len(chunk)
                if size > 8 * 1024 * 1024:
                    raise GitHubError("ci_log_too_large")
                tail.extend(chunk)
                del tail[:-80000]
            return feedback_text(tail.decode("utf-8", errors="replace"))

        with httpx.Client(timeout=30, transport=self.transport, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", path, headers={"Authorization": "Bearer " + self.token()}) as response:
                if response.status_code == 302:
                    url = httpx.URL(response.headers["location"])
                    if (url.scheme != "https" or url.userinfo or not url.host
                            or not url.host.endswith((".blob.core.windows.net", ".actions.githubusercontent.com"))):
                        raise ValueError("unexpected_ci_log_host")
                    path = str(url)
                elif response.status_code == 200:
                    return {"head": receipt["head"], "steps": steps, "log_excerpt": read_log(response)}
                else:
                    raise GitHubError("ci_log_unavailable")
            # A signed blob URL authenticates itself; never forward the installation token.
            with client.stream("GET", path) as stream:
                log = read_log(stream)
        return {"head": receipt["head"], "steps": steps, "log_excerpt": log}

    def pull_request(self, job):
        receipt, payload = job["receipt"], job["payload"]
        # Re-read both refs: earlier CI does not authorize a moved branch or a new base.
        for branch, expected in [(receipt["branch"], receipt["head"]), (self.config.base, receipt["base"])]:
            ref = self.request("GET", "/git/ref/heads/" + quote(branch, safe=""))
            if ref["object"]["sha"] != expected:
                raise ValueError("pr_ref_moved")
        if self.ci(receipt)["state"] != "passed":
            raise ValueError("ci_no_longer_passed")
        head = self.config.repository.split("/")[0] + ":" + receipt["branch"]
        existing = self.request("GET", "/pulls?state=all&head=" + quote(head, safe="")
                                + "&base=" + quote(self.config.base, safe=""))
        if existing:
            if len(existing) != 1 or existing[0]["head"]["sha"] != receipt["head"]:
                raise ValueError("pr_collision")
            return {"url": existing[0]["html_url"], "number": existing[0]["number"], "state": existing[0]["state"]}
        finding = payload["finding"]
        evidence = "\n".join("- `" + key + "`" for key in finding["evidence_keys"])
        evaluation = payload.get("evaluation", {})
        plan = finding.get("evaluation", {})
        if plan.get("mode") == "regression":
            evaluation = {"mode": "regression", "state": "ci_passed", "base_assertion_reproduced": True,
                          "candidate_suite_passed": True,
                          "scope": "New regression fails on the exact parent; patched service suite passes. "
                                   "This does not prove general model behavior or financial expertise."}
        comparison = json.dumps(evaluation, ensure_ascii=False, indent=2)
        body = (f"## Problem\n\n{finding['problem']}\n\n## Reproduction\n\n{finding['reproduction']}"
                f"\n\n## Expected behavior\n\n{finding['expected']}\n\n## Change\n\n{payload['summary']}"
                f"\n\n## Diagnosis and frozen evaluation\n\nCategory: `{finding.get('category', 'legacy')}`.\n\n"
                f"{finding.get('hypothesis', '')}\n\n{plan.get('success_criterion', '')}\n\n"
                f"Plan digest: `{payload.get('evaluation_plan_digest')}`; "
                f"history digest: `{payload.get('review_digest')}`.\n\n```json\n{comparison}\n```"
                f"\n\n## Evidence references\n\n{evidence}\n\n## Validation\n\n"
                f"[CI run]({receipt['ci']['url']}) on `{receipt['head']}`.\n"
                f"Base `{receipt['base']}`; patch digest `{payload['patch_digest']}`.\n\n"
                f"Candidate attempt: `{payload.get('patch_attempt', 1)}`; prior attempts: "
                f"`{len(payload.get('candidate_attempts', []))}`. "
                "The original functional criterion is unchanged across candidate repairs.\n\n"
                "Model-generated change; human review required. No merge or deployment was performed. "
                "Source conversations stay in the company database; summaries may still require redaction.\n")
        if SECRET.search(body):
            raise ValueError("possible_secret_in_pr")
        # On an ambiguous POST, the runner blocks. An operator reconciles with the head query above.
        # Do not blindly replay PR creation: GitHub does not offer an idempotency key for this endpoint.
        try:
            pr = self.request("POST", "/pulls", data={"title": finding["title"], "head": receipt["branch"],
                              "base": self.config.base, "body": body, "draft": True, "maintainer_can_modify": False})
        except (GitHubError, httpx.HTTPError):
            raise GitHubError("pr_write_requires_reconciliation") from None
        return {"url": pr["html_url"], "number": pr["number"], "state": pr["state"]}

    def validate_application(self, job):
        saved, payload = job["receipt"], job["payload"]
        pr = self.request("GET", f"/pulls/{saved['pr']['number']}")
        if (pr["head"]["sha"] != saved["head"] or pr["head"]["ref"] != saved["branch"]
                or pr["head"]["repo"]["full_name"] != self.config.repository
                or pr["base"]["ref"] != self.config.base or pr["state"] != "open"):
            raise ValueError("approved_pr_changed_or_closed")
        changes = payload["changes"]
        files = self.request("GET", f"/pulls/{saved['pr']['number']}/files?per_page=100")
        if len(files) != len(changes) or pr["changed_files"] != len(files):
            raise ValueError("approved_patch_file_set_changed")
        for item in files:
            path = item["filename"]
            if (path not in changes or item["status"] not in {"added", "modified"}
                    or not writable(path, new=item["status"] == "added")
                    or item["sha"] != blob_sha(changes[path])):
                raise ValueError("approved_patch_content_changed")
        requires_deployment = any(p.startswith("src/") for p in changes)
        base = self.request("GET", "/git/ref/heads/main")["object"]["sha"]
        # PR.base.sha can retain the original base snapshot after main advances. The live ref,
        # unchanged document blobs, and eventual merge parents are the authoritative checks.
        if base != saved["base"]:
            # Unchanged documentation can survive an unrelated operator release. Executable code
            # must be retested on its new base, not silently approved with old integration evidence.
            if requires_deployment:
                raise ValueError("code_base_moved_retest_required")
            for path, original in payload["originals"].items():
                current = self.request("GET", "/contents/" + quote(path, safe="/") + "?ref=" + base)
                if current["sha"] != blob_sha(original):
                    raise ValueError("documentation_base_changed")
        ci = self.ci(saved)
        if ci["state"] == "pending" or pr.get("mergeable") is None:
            return None
        if ci["state"] != "passed" or not pr["mergeable"]:
            raise ValueError("approved_ci_or_mergeability_failed")
        return {"validated_base": base, "ci": ci, "requires_deployment": requires_deployment}

    def merge_application(self, job, receipt):
        saved = job["receipt"]
        number = saved["pr"]["number"]
        pr = self.request("GET", f"/pulls/{number}")
        if (pr["head"]["sha"] != saved["head"] or pr["base"]["ref"] != self.config.base
                or self.request("GET", "/git/ref/heads/main")["object"]["sha"] != receipt["validated_base"]):
            raise ValueError("approved_refs_moved_before_merge")
        if pr["draft"]:
            result = self._http("POST", "/graphql", self.token(), data={
                "query": "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{isDraft}}}",
                "variables": {"id": pr["node_id"]}})
            if result.get("errors") or result["data"]["markPullRequestReadyForReview"]["pullRequest"]["isDraft"]:
                raise GitHubError("pr_ready_failed")
        try:
            self.request("PUT", f"/pulls/{number}/merge", data={"sha": saved["head"], "merge_method": "merge"})
        except (GitHubError, httpx.HTTPError):
            # A lost acknowledgement may conceal a successful merge. Read back; never retry PUT.
            pass
        return self.reconcile_application(job, receipt)

    def reconcile_application(self, job, receipt):
        pr = self.request("GET", f"/pulls/{job['receipt']['pr']['number']}")
        if not pr.get("merged") or pr["head"]["sha"] != job["receipt"]["head"]:
            raise GitHubError("merge_not_confirmed_requires_reconciliation")
        commit = self.request("GET", "/git/commits/" + pr["merge_commit_sha"])
        if [p["sha"] for p in commit["parents"]] != [receipt["validated_base"], job["receipt"]["head"]]:
            raise GitHubError("merge_parents_changed_requires_review")
        return {"merge_commit": commit["sha"], "pr_url": pr["html_url"]}

    def archive(self, commit):
        # API redirect contains a temporary download URL; it never reaches logs or model context.
        with httpx.Client(timeout=60, trust_env=False, transport=self.transport) as client:
            response = client.get(f"https://api.github.com/repos/{self.config.repository}/tarball/{commit}",
                                  headers={"Authorization": "Bearer " + self.token()})
            if response.status_code != 302:
                raise GitHubError("archive_redirect_failed")
            url = httpx.URL(response.headers["location"])
            if url.scheme != "https" or url.host != "codeload.github.com":
                raise GitHubError("unexpected_archive_host")
            with client.stream("GET", url) as stream:
                if stream.status_code != 200:
                    raise GitHubError("archive_download_failed")
                data = bytearray()
                for chunk in stream.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 32 * 1024 * 1024:
                        raise GitHubError("archive_too_large")
        return bytes(data)
