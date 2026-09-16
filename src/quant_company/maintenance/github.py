"""Privileged Git broker. It never executes candidate code or exposes credentials to a model."""

import base64
import json
import subprocess
import time
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .policy import SECRET, WORKFLOW, MaintenanceConfig, writable


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
            if item.get("size", 0) > 65000:
                raise ValueError("source_file_too_large")
            blob = self.request("GET", "/git/blobs/" + item["sha"])
            if blob["encoding"] != "base64":
                raise ValueError("unsupported_blob_encoding")
            output[path] = base64.b64decode(blob["content"]).decode("utf-8")
        if sum(map(len, output.values())) > 65000:
            raise ValueError("source_context_too_large")
        return output

    def publish(self, job, payload):
        branch = "maintenance/" + str(job["id"])
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
            return {"state": "failed", "run_id": run["id"], "url": run["html_url"]}
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
        body = (f"## Problem\n\n{finding['problem']}\n\n## Reproduction\n\n{finding['reproduction']}"
                f"\n\n## Expected behavior\n\n{finding['expected']}\n\n## Change\n\n{payload['summary']}"
                f"\n\n## Evidence references\n\n{evidence}\n\n## Validation\n\n"
                f"[CI run]({receipt['ci']['url']}) on `{receipt['head']}`.\n"
                f"Base `{receipt['base']}`; patch digest `{payload['patch_digest']}`.\n\n"
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
