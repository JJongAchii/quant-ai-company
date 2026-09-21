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

    @app.post("/v1/research/worker/jobs/{job_id}/heartbeat", dependencies=[Depends(worker)])
    def heartbeat(job_id: UUID, value: WorkerUpdate):
        return store.heartbeat(str(job_id), value)

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
