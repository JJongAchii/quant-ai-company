import asyncio
import hmac
import json
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import Field

from .company import Company, PolicyError
from .config import Settings
from .contracts import HumanRequest, StrictModel
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


def create_app(settings: Settings | None = None, company: Company | None = None,
               credentials: dict | None = None) -> FastAPI:
    settings = settings or Settings()
    company = company or Company(settings)
    token = settings.require_operator_token()
    ingress = SlackIngress(settings, company, credentials)
    app = FastAPI(title="Quant Company", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.company = company

    def operator(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Operator authentication required")

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
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Expected object")
        except (PolicyError, ValueError):
            raise HTTPException(401, "Invalid Slack request") from None
        # ACK only after the transaction commits. No model call in this request path.
        return await asyncio.to_thread(ingress.accept, role, payload, credential)

    @app.get("/v1/agents", dependencies=[Depends(operator)])
    def agents():
        return [role.model_dump() for role in company.roles.values()]

    @app.get("/v1/projects", dependencies=[Depends(operator)])
    def projects():
        return company.list_projects()

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
    def retry(task_id: str):
        return company.retry_task(task_id)

    @app.post("/v1/sources", dependencies=[Depends(operator)])
    def source(value: SourceInput):
        company.put_source(**value.model_dump())
        return {"ok": True, "source_id": value.source_id}

    @app.post("/v1/memories/{memory_id}/review", dependencies=[Depends(operator)])
    def review(memory_id: str, value: MemoryReview):
        company.review_memory(memory_id, value.approve, "operator", value.share)
        return {"ok": True}

    return app
