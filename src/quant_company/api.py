import asyncio
import hmac
import json
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import Field

from .company import Company, PolicyError
from .config import Settings
from .contracts import HumanRequest, StrictModel
from .research.contracts import ResearchRevalidation
from .slack import SlackIngress


class SourceInput(StrictModel):
    source_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=300)
    uri: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=100000)
    available_at: datetime
    project_id: str | None = None
    approved: bool = False
    synthetic: bool = False


class MemoryReview(StrictModel):
    approve: bool
    share: bool = False


class RetryInput(StrictModel):
    reconciliation_note: str = Field(min_length=1, max_length=1000)


class StaffReviewInput(StrictModel):
    disposition: str = Field(pattern="^(confirmed|disputed)$")
    note: str = Field(min_length=10, max_length=2000)


def create_app(settings: Settings | None = None, company: Company | None = None,
               credentials: dict | None = None) -> FastAPI:
    settings = settings or Settings()
    company = company or Company(settings)
    token = settings.require_operator_token()
    ingress = SlackIngress(settings, company, credentials)
    app = FastAPI(title="Quant Company", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.company = company
    from .research.api import register_routes

    register_routes(app, company)

    def operator(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Operator authentication required")

    @app.get("/v1/videos", dependencies=[Depends(operator)])
    def videos():
        from .video.store import VideoStore

        return VideoStore(company).status()

    @app.exception_handler(PolicyError)
    async def policy_error(request: Request, exc: PolicyError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"error": str(exc)})

    @app.get("/healthz")
    async def health():
        try:
            await asyncio.to_thread(company.db.health)
        except Exception:
            raise HTTPException(503, "Database unavailable") from None
        return {"ok": True}

    @app.post("/slack/events/{role}")
    async def slack_event(role: str, request: Request):
        body = await request.body()
        try:
            credential = ingress.verify(role, body, request.headers.get("x-slack-request-timestamp", ""),
                                        request.headers.get("x-slack-signature", ""))
            if request.headers.get("content-type", "").split(";", 1)[0] == "application/x-www-form-urlencoded":
                from urllib.parse import parse_qs

                form = parse_qs(body.decode(), strict_parsing=True, max_num_fields=1)
                payload = json.loads(form["payload"][0])
            else:
                payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Expected object")
        except (PolicyError, ValueError, KeyError):
            raise HTTPException(401, "Invalid Slack request") from None
        # ACK only after the transaction commits. No model call in this request path.
        return await asyncio.to_thread(ingress.accept, role, payload, credential)

    @app.get("/v1/agents", dependencies=[Depends(operator)])
    def agents():
        if company.settings.model_assignments_enabled:
            from .model_policy import effective_role

            with company.db.transaction() as conn:
                return [effective_role(company, conn, name).model_dump() for name in company.roles]
        return [role.model_dump() for role in company.roles.values()]

    @app.get("/v1/model-assignments", dependencies=[Depends(operator)])
    def model_assignments():
        from .model_policy import status

        with company.db.transaction() as conn:
            return status(company, conn)

    @app.post("/v1/research/jobs/{job_id}/revalidate", dependencies=[Depends(operator)])
    def research_revalidate(job_id: str, value: ResearchRevalidation):
        from uuid import UUID

        from .research.store import ResearchStore

        try:
            identity = str(UUID(job_id))
        except ValueError:
            raise HTTPException(422, "Invalid research job ID") from None
        return ResearchStore(company).revalidate(identity, value)

    @app.get("/v1/staff", dependencies=[Depends(operator)])
    def staff_status(employee: str | None = None):
        from .staff.store import status

        with company.db.transaction() as conn:
            return status(conn, company, settings.slack_allowed_users[0] if settings.slack_allowed_users else "", employee)

    @app.post("/v1/staff/runs/{run_id}/review", dependencies=[Depends(operator)])
    def staff_review(run_id: str, value: StaffReviewInput):
        from .staff.store import StaffStore

        try:
            StaffStore(company).review(run_id, value.disposition, value.note)
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc
        return {"ok": True}

    @app.get("/v1/projects", dependencies=[Depends(operator)])
    def projects():
        return company.list_projects()

    @app.get("/v1/data-watch", dependencies=[Depends(operator)])
    def data_watch_status():
        from .data_watch.store import DataWatchStore

        store = DataWatchStore(company)
        return {"authorized": store.authorized(), **store.status()}

    @app.get("/v1/projects/{project_id}", dependencies=[Depends(operator)])
    def project(project_id: str):
        return company.project_state(project_id)

    @app.post("/v1/requests", dependencies=[Depends(operator)])
    def submit(value: HumanRequest):
        return company.ingest(event_key="operator:" + value.request_id, owner="operator", text=value.text,
                              agent=value.agent, project_id=value.project_id)

    @app.post("/v1/projects/{project_id}/revise", dependencies=[Depends(operator)])
    def revise(project_id: str, value: HumanRequest):
        state = company.project_state(project_id)
        return company.ingest(event_key="operator:" + value.request_id, owner=state["project"]["owner_user"],
                              text=value.text, agent=value.agent, project_id=project_id, revise=True)

    @app.post("/v1/tasks/{task_id}/retry", dependencies=[Depends(operator)])
    def retry(task_id: str, value: RetryInput):
        return company.retry_task(task_id, reconciliation_note=value.reconciliation_note)

    @app.post("/v1/sources", dependencies=[Depends(operator)])
    def source(value: SourceInput):
        company.put_source(**value.model_dump())
        return {"ok": True, "source_id": value.source_id}

    @app.post("/v1/memories/{memory_id}/review", dependencies=[Depends(operator)])
    def review(memory_id: str, value: MemoryReview):
        company.review_memory(memory_id, value.approve, "operator", value.share)
        return {"ok": True}

    return app
