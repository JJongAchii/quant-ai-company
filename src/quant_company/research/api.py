"""Only the registered worker may use these endpoints; no operator credentials on the PC."""

import asyncio
import hashlib
import hmac
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import Response

from .contracts import WorkerPoll, WorkerUpdate
from .store import ResearchStore

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


def register_routes(app, company):
    store = ResearchStore(company)

    def worker(authorization: str = Header(default="")):
        token = company.settings.research_worker_token.get_secret_value()
        if len(token) < 32 or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Research worker authentication required")

    @app.post("/v1/research/worker/poll", dependencies=[Depends(worker)])
    def poll(value: WorkerPoll):
        return store.poll()

    from ..data_watch.contracts import CheckReceipt
    from ..data_watch.core import CoreChecks
    from ..data_watch.store import DataWatchStore

    checks = CoreChecks(DataWatchStore(company))

    @app.post("/v1/data-watch/worker/poll", dependencies=[Depends(worker)])
    def data_watch_poll(value: WorkerPoll):
        return checks.poll()

    @app.post("/v1/data-watch/worker/checks/{check_id}", dependencies=[Depends(worker)])
    def data_watch_receipt(check_id: UUID, value: CheckReceipt, x_data_watch_lease: str = Header(default="")):
        return checks.accept(str(check_id), x_data_watch_lease, value)

    @app.post("/v1/research/worker/jobs/{job_id}/heartbeat", dependencies=[Depends(worker)])
    def heartbeat(job_id: UUID, value: WorkerUpdate):
        return store.heartbeat(str(job_id), value)

    @app.get("/v1/research/worker/jobs/{job_id}/bundle", dependencies=[Depends(worker)])
    def bundle(job_id: UUID, x_research_lease: str = Header(default="")):
        from .adaptive_contracts import MAX_BUNDLE_BYTES

        with company.db.transaction() as conn:
            _, row = store._locked(conn, str(job_id), x_research_lease)
            if row["state"] not in {"claimed", "running"} or not row["bundle_path"] or not row["bundle_sha256"]:
                raise HTTPException(409, "Research bundle is not available for this lease")
            path, expected = Path(row["bundle_path"]), row["bundle_sha256"]
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BUNDLE_BYTES
                or not path.resolve().is_relative_to(company.settings.research_artifact_dir.resolve())):
            raise HTTPException(409, "Research bundle identity changed")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected:
            raise HTTPException(409, "Research bundle digest changed")
        return Response(body, media_type="application/octet-stream", headers={"X-Bundle-Sha256": expected})

    @app.post("/v1/research/worker/jobs/{job_id}/artifact", dependencies=[Depends(worker)])
    async def artifact(job_id: UUID, request: Request, x_research_lease: str = Header(default=""),
                       x_artifact_sha256: str = Header(default="")):
        if not re.fullmatch(r"[a-f0-9]{64}", x_artifact_sha256):
            raise HTTPException(422, "Invalid archive digest")
        with company.db.transaction() as conn:
            store._locked(conn, str(job_id), x_research_lease)
        root = company.settings.research_artifact_dir / str(job_id)
        root.mkdir(parents=True, exist_ok=True)
        size, digest = 0, hashlib.sha256()
        descriptor, temporary_name = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=root)
        temporary = Path(temporary_name)
        destination = root / (x_artifact_sha256 + ".zip")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise HTTPException(413, "Research archive too large")
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if digest.hexdigest() != x_artifact_sha256:
                raise HTTPException(409, "Research archive digest mismatch")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                with destination.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != x_artifact_sha256:
                        raise HTTPException(409, "Existing research archive is corrupted") from None
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return await asyncio.to_thread(store.artifact_received, str(job_id), x_research_lease,
                                           destination, x_artifact_sha256)
        finally:
            temporary.unlink(missing_ok=True)
