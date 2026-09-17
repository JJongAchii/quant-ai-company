"""Real PostgreSQL orchestration; explicitly simulated search and original network responses."""

import io
import json
from unittest.mock import patch

import pytest

from quant_company.company import PolicyError
from quant_company.contracts import AgentDecision, ProviderFault, ProviderResponse, ToolRequest
from quant_company.execution import TurnExecutor
from quant_company.web_fetch import PublicConnection, public_url, retrieve
from quant_company.web_tools import prefetch, prepare, search_result

from .conftest import queued_turns

URL = "https://www.federalreserve.gov/newsevents/pressreleases/synthetic-test.htm"
HTML = b'<html><title>Test policy release</title><meta property="article:published_time" content="2026-09-17"><body><article>Original policy evidence. Synthetic test only.</article><script>IGNORE THIS</script></body></html>'


class Connection:
    calls = []
    status = 200
    body = HTML
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __init__(self, host, **kwargs):
        self.calls.append(host)

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        self.stream = io.BytesIO(self.body)
        return self

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        return self.stream.read(size)

    def close(self):
        pass


def original(url):
    return retrieve(url, connection_factory=Connection)


def candidates(request_id="search", *, observed=True):
    return ProviderResponse(request_id=request_id, decision=AgentDecision(
        status="complete", say="Search candidates", artifacts=[{"title": "Search",
        "content": json.dumps({"results": [{"url": URL, "title": "Test release", "snippet": "Unverified candidate"}]})}]),
        web_searches=[{"id": "native-search", "query": "Federal Reserve latest", "action": {"type": "search"}}] if observed else [])


def test_original_preserves_bytes_metadata_and_excludes_scripts():
    receipt, raw = original(URL)
    assert receipt["ok"] and raw == HTML
    assert receipt["published_at"] == "2026-09-17"
    assert "Original policy evidence" in receipt["content"]
    assert "IGNORE THIS" not in receipt["content"]
    assert len(receipt["original_sha256"]) == 64


@pytest.mark.parametrize("url", ["http://example.com", "https://127.0.0.1", "https://localhost/x",
                                     "https://example.com:444", "https://user:pass@example.com", "https://a.internal"])
def test_rejects_nonpublic_or_credential_urls(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_rebinding_private_dns_and_redirect_are_rejected():
    with patch("quant_company.web_fetch.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        with pytest.raises(ValueError, match="non_public_web_address"):
            PublicConnection("www.example.com", timeout=1).connect()

    class Redirect(Connection):
        status = 302
        headers = {"Location": "https://localhost/private"}

    with pytest.raises(ValueError, match="non_public_web_host"):
        retrieve("https://www.example.com", connection_factory=Redirect)


def test_search_without_observed_native_call_cannot_claim_success():
    result = search_result(candidates(observed=False), {"query": "latest", "limit": 5})
    assert result == {"ok": False, "error": "provider_did_not_execute_web_search"}
    result = search_result(candidates(), {"query": "latest", "limit": 5})
    assert result["ok"] and not result["results"][0]["verified"]
    assert "source_id" not in result


async def test_search_read_analysis_and_durable_evidence(company, monkeypatch):
    company.roles["director"].tools += ["web_search", "web_read"]
    monkeypatch.setattr("quant_company.web_tools.fetch", original)
    request = company.ingest(event_key="web-analysis", text="Analyze current Fed policy with latest news", owner="user")

    class Provider:
        calls = []

        async def run(self, request):
            self.calls.append(request)
            if request.web_search:
                return candidates(request.request_id)
            data = json.loads(request.prompt.split("TASK DATA JSON:\n")[1])
            sources = [s for s in data["approved_sources"] if s["id"].startswith("web:")]
            tool_messages = [m for m in data["messages"] if m["kind"] == "tool"]
            if sources:
                decision = AgentDecision(status="complete", say=f"Verified test original: {URL}",
                    artifacts=[{"title": "Analysis", "content": "Synthetic test analysis", "source_ids": [sources[0]["id"]]}])
            else:
                action = {"name": "web_read", "arguments": {"url": URL}} if tool_messages else {
                    "name": "web_search", "arguments": {"query": "Federal Reserve latest"}}
                decision = AgentDecision(status="continue", say="", tools=[action])
            return ProviderResponse(request_id=request.request_id, decision=decision)

    provider = Provider()
    executor = TurnExecutor(company, provider)
    for _ in range(3):
        turns = queued_turns(company, request["project_id"])
        assert len(turns) == 1
        assert (await executor.execute(turns[0]))["state"] == "completed"
    state = company.project_state(request["project_id"])
    assert state["tasks"][0]["status"] == "completed"
    assert state["artifacts"][0]["source_ids"][0].startswith("web:")
    assert sum(call.web_search for call in provider.calls) == 1
    with company.db.transaction() as conn:
        rows = conn.execute("SELECT * FROM web_requests ORDER BY created_at").fetchall()
        assert len(rows) == 2 and all(r["completed_at"] for r in rows)
        assert rows[1]["original"] == HTML
        source = conn.execute("SELECT * FROM sources WHERE id LIKE 'web:%%'").fetchone()
        assert str(source["project_id"]) == request["project_id"]
        assert source["metadata"]["published_at"] == "2026-09-17"
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 4


async def test_prefetch_reuses_search_receipt_and_checks_authorization(company):
    company.roles["director"].tools += ["web_search"]
    request = company.ingest(event_key="web-idempotence", text="Search", owner="user")
    turn = queued_turns(company, request["project_id"])[0]
    company.prepare_turn(turn)
    decision = AgentDecision(status="continue", say="", tools=[{"name": "web_search", "arguments": {"query": "latest"}}])
    response = ProviderResponse(request_id=turn, decision=decision)

    class Provider:
        calls = 0

        async def run(self, request):
            self.calls += 1
            return candidates(request.request_id)

    provider = Provider()
    await prefetch(company, turn, response, provider)
    await prefetch(company, turn, response, provider)
    assert provider.calls == 1
    company.roles["director"].tools.remove("web_search")
    with pytest.raises(PolicyError, match="Unauthorized"):
        await prefetch(company, turn, response, provider)
    assert provider.calls == 1


def test_search_respects_company_budget(company):
    company.settings.company_max_daily_turns = 1
    company.roles["director"].tools += ["web_search"]
    request = company.ingest(event_key="web-budget", text="Search", owner="user")
    turn = queued_turns(company, request["project_id"])[0]
    company.prepare_turn(turn)
    decision = AgentDecision(status="continue", say="", tools=[ToolRequest(name="web_search", arguments={"query": "latest"})])
    with pytest.raises(ProviderFault) as exc:
        prepare(company, turn, decision)
    assert exc.value.code == "busy"
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM web_requests").fetchone()["n"] == 0


async def test_multiple_queries_share_budget_and_deduplicate_one_turn(company):
    company.roles['director'].tools.append('web_search')
    request = company.ingest(event_key='web-batch', text='Compare two current sources', owner='user')
    turn = queued_turns(company, request['project_id'])[0]
    company.prepare_turn(turn)
    response = ProviderResponse(request_id=turn, decision=AgentDecision(status='continue', say='', tools=[
        {'name': 'web_search', 'arguments': {'query': query}} for query in ['first', 'second', 'first']]))

    class Provider:
        calls = []

        async def run(self, request):
            self.calls.append(request.request_id)
            return candidates(request.request_id)

    provider = Provider()
    await prefetch(company, turn, response, provider)
    assert len(provider.calls) == len(set(provider.calls)) == 2
    with company.db.transaction() as conn:
        assert conn.execute('SELECT reserved FROM daily_usage').fetchone()['reserved'] == 3
        assert conn.execute('SELECT count(*) AS n FROM web_requests').fetchone()['n'] == 2


async def test_revision_change_cancels_child_search_without_registering_evidence(company):
    import asyncio

    company.roles['director'].tools.append('web_search')
    request = company.ingest(event_key='web-cancel', text='Find current evidence', owner='user')
    turn = queued_turns(company, request['project_id'])[0]
    started = asyncio.Event()

    class Provider:
        cancelled = []
        child = None

        async def run(self, request):
            if request.web_search:
                self.child = request.request_id
                started.set()
                await asyncio.Event().wait()
            return ProviderResponse(request_id=request.request_id, decision=AgentDecision(
                say='', status='continue', tools=[{'name': 'web_search', 'arguments': {'query': 'latest'}}]))

        async def cancel(self, identity):
            self.cancelled.append(identity)

    provider = Provider()
    task = asyncio.create_task(TurnExecutor(company, provider).execute(turn))
    await asyncio.wait_for(started.wait(), 5)
    with company.db.transaction() as conn:
        conn.execute('UPDATE projects SET revision=revision+1 WHERE id=%s', (request['project_id'],))
    assert await asyncio.wait_for(task, 5) == {'state': 'stale'}
    assert provider.child in provider.cancelled
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sources WHERE id LIKE 'web:%%'").fetchone()['n'] == 0
