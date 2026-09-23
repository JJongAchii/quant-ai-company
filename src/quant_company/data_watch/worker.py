"""Optional lane in the existing pull worker; a bounded subprocess never delays research heartbeats."""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from ..research.recipes import load_recipe
from ..research.worker import atomic_json
from .contracts import CheckAssignment, CheckReceipt, CoreScope


class DataWatchTransport:
    def __init__(self, config, client):
        self.config, self.client = config, client
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="data-watch-reader")
        self.pending = None
        self.assignment = None

    def read(self, assignment):
        def unavailable(error):
            return CheckReceipt(check_id=assignment.id, scope_digest=assignment.scope_digest,
                                checked_at=datetime.now(UTC), state="unavailable", error=error)

        scope = CoreScope.from_recipe(load_recipe())
        if assignment.scope != scope or assignment.scope_digest != scope.digest():
            return unavailable("scope_not_registered")
        root = self.config.state_dir / "data-watch"
        directory = root / str(assignment.id)
        output = directory / "receipt.json"
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if output.exists():
                if output.stat().st_size > 32768:
                    return unavailable("reader_failed")
                cached = CheckReceipt.model_validate_json(output.read_text())
                if cached.check_id != assignment.id or cached.scope_digest != assignment.scope_digest:
                    return unavailable("reader_failed")
                return cached
            request = directory / "assignment.json"
            atomic_json(request, assignment.model_dump(mode="json"))
        except (OSError, ValueError):
            return unavailable("reader_unavailable")
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
        env.update(PYTHONPATH=str(self.config.company_repo / "src"), PYTHONNOUSERSITE="1",
                   OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        error = "reader_failed"
        try:
            completed = subprocess.run([str(self.config.research_python), "-B", "-m", "quant_company.data_watch.checker",
                str(request), str(self.config.input_source), str(directory), str(output)],
                cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=190, check=False)
            if completed.returncode == 0 and output.exists() and output.stat().st_size <= 32768:
                return CheckReceipt.model_validate_json(output.read_text())
        except subprocess.TimeoutExpired:
            error = "reader_timeout"
        except (OSError, ValueError):
            error = "reader_unavailable"
        result = unavailable(error)
        try:
            atomic_json(output, result.model_dump(mode="json"))
        except OSError:
            pass  # The failed read has no external write to replay; the future retains this exact receipt.
        return result

    def step(self):
        if self.pending:
            if not self.pending.done():
                return
            receipt = self.pending.result()
            response = self.client.post(f"/v1/data-watch/worker/checks/{self.assignment.id}",
                headers={"X-Data-Watch-Lease": self.assignment.lease_token}, json=receipt.model_dump(mode="json"))
            if response.status_code != 409:
                response.raise_for_status()
                if response.json().get("ok") is not True:
                    raise ValueError("data_watch_receipt_not_acknowledged")
            self.pending, self.assignment = None, None
            return
        response = self.client.post("/v1/data-watch/worker/poll", json={"worker_id": "worker"})
        response.raise_for_status()
        value = response.json()["assignment"]
        if value:
            self.assignment = CheckAssignment.model_validate(value)
            self.pending = self.pool.submit(self.read, self.assignment)
