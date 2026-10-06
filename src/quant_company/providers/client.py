"""Company-side client. It sends requests; it never retries inference itself."""

import json
import math
import re

import httpx
from pydantic import ValidationError

from quant_company.contracts import ProviderFault, ProviderRequest, ProviderResponse
from quant_company.providers.codex_runner import strict_json

FAULT_MESSAGES = {
    "quota": "Model subscription allowance is temporarily unavailable.",
    "auth": "The model runtime requires authentication.",
    "busy": "The model runtime is executing another turn.",
    "uncertain": "The model turn requires reconciliation before a replacement call.",
    "timeout": "The model turn timed out and requires operator review.",
    "invalid_output": "The model output did not satisfy the company decision contract.",
    "unavailable": "The model runtime is unavailable.",
}


class RuntimeClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: float = 360, *,
                 transport: httpx.AsyncBaseTransport | None = None, expected_provider: str = "codex"):
        if not token or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Runtime token and positive finite timeout are required")
        url = httpx.URL(base_url)
        if url.scheme not in ("http", "https") or not url.host or url.userinfo:
            raise ValueError("Use a private HTTP(S) runtime URL without embedded credentials")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        if expected_provider not in {"codex", "claude"}:
            raise ValueError("Unknown runtime provider")
        self.expected_provider = expected_provider

    async def cancel(self, request_id: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id):
            raise ValueError("Invalid cancellation request ID")
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 15), transport=self.transport,
                                         follow_redirects=False, trust_env=False) as client:
                response = await client.post(f"{self.base_url}/v1/turns/{request_id}/cancel",
                                             headers={"Authorization": f"Bearer {self.token}"})
                if response.status_code != 200:
                    raise ProviderFault("uncertain", "Cancellation was not acknowledged by the runtime.")
                data = strict_json(response.content)
                if (not isinstance(data, dict) or data.get("request_id") != request_id
                        or data.get("status") not in ("cancelled", "completed", "uncertain")):
                    raise ValueError("Invalid cancellation response")
                return data["status"]
        except (httpx.HTTPError, ValueError, TypeError):
            raise ProviderFault("uncertain", "Cancellation was not acknowledged by the runtime.") from None

    async def accounts(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=40, transport=self.transport,
                                         follow_redirects=False, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/v1/accounts",
                                            headers={"Authorization": f"Bearer {self.token}"})
            data = strict_json(response.content)
            rows = data["accounts"]
            if (response.status_code != 200 or not isinstance(rows, list) or len(rows) != 2
                    or {r["profile"] for r in rows} != {"primary", "backup"}
                    or any(set(r) != {"profile", "authentication"} or r["authentication"] not in
                           {"chatgpt", "needs_login", "unavailable"} for r in rows)):
                raise ValueError("Invalid account status")
            return rows
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderFault("unavailable", "Account authentication could not be checked.") from None

    async def models(self, profile: str) -> list[dict]:
        from .model_catalog import validate_models

        if profile not in {"primary", "backup"}:
            raise ValueError("Unknown account profile")
        try:
            async with httpx.AsyncClient(timeout=45, transport=self.transport,
                                         follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", f"{self.base_url}/v1/models/{profile}",
                                         headers={"Authorization": f"Bearer {self.token}"}) as response:
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 512 * 1024:
                            raise ValueError("Catalog response too large")
                    data = strict_json(body)
                    if response.status_code != 200 or data["profile"] != profile:
                        raise ValueError("Invalid catalog response")
                    return validate_models(data["models"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderFault("unavailable", "Model availability could not be checked.") from None

    async def run(self, request: ProviderRequest, *, account: dict | None = None) -> ProviderResponse:
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = request.model_dump(mode="json")
        if request.output_contract == "agent_decision":
            # Legacy callers remain compatible with older runtime deployments.
            payload.pop("output_contract", None)
        # Other pinned subscription runtimes still use the older strict request
        # schema. Ordinary requests retain their original wire shape.
        if request.session is None:
            payload.pop("session", None)
        if account is not None:
            headers.update({"X-Company-Account": account["profile"], "X-Company-Account-Revision": str(account["revision"])})
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport,
                                         follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", f"{self.base_url}/v1/turns",
                                         headers=headers,
                                         json=payload) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise ProviderFault("uncertain", FAULT_MESSAGES["uncertain"])
                    data = strict_json(body)
                    if response.status_code != 200:
                        code = data.get("code") if isinstance(data, dict) else None
                        if code not in FAULT_MESSAGES:
                            code = "uncertain"
                        retry = data.get("retry_after_seconds", 0) if isinstance(data, dict) else 0
                        if type(retry) is not int or retry < 0 or retry > 604800:
                            retry = 0
                        raise ProviderFault(code, FAULT_MESSAGES[code], retry)
                    result = ProviderResponse.model_validate(data)
                    if result.request_id != request.request_id or result.provider != self.expected_provider:
                        raise ValueError("Mismatched provider result")
                    json.dumps(result.model_dump(mode="json"), allow_nan=False)
                    return result
        except httpx.ConnectError:
            raise ProviderFault("unavailable", FAULT_MESSAGES["unavailable"]) from None
        except (httpx.HTTPError, ValueError, TypeError, ValidationError):
            # A timeout/lost response may hide a completed inference. The caller
            # can safely recover by querying the same ID, never by inventing one.
            raise ProviderFault("uncertain", FAULT_MESSAGES["uncertain"]) from None
