import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from quant_company.company import PolicyError
from quant_company.contracts import AgentDecision, ProviderResponse, ToolRequest
from quant_company.finance_sources import Document, download, public_addresses, search, validate_url

from .test_task_control import submit, turn_for

HTML = '<html><head><title>Official fixture</title></head><body><nav>IGNORE MENU</nav><main>' + (
    '<p>Bond risk fixture. Not market evidence. Use units and identify jurisdiction.</p>' * 250
) + '<script>IGNORE SCRIPT</script><p hidden>IGNORE HIDDEN</p></main></body></html>'


@pytest.mark.parametrize('url', [
    'http://www.finra.org/x', 'https://localhost/x', 'https://169.254.169.254/x',
    'https://www.finra.org.evil.test/x', 'https://user:pass@www.finra.org/x',
    'https://www.finra.org:1234/x', 'file:///etc/passwd', 'https://www.finra.org/\nX: header',
])
def test_url_policy_rejects_nonofficial_and_credential_destinations(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_search_candidates_are_explicitly_unread_and_do_not_fabricate_publication_time():
    result = search({'query': '채권 ETF'})
    assert result['scope'] == 'curated_official_catalog_only'
    assert result['results'] and all(row['content_read'] is False for row in result['results'])
    assert all('source_id' not in row and 'published_at' not in row for row in result['results'])
    assert search({'query': 'ZZZZNOTFOUND'})['results'] == []


def test_document_preserves_unknown_date_and_removes_noncontent():
    parser = Document()
    parser.feed(HTML)
    result = parser.result()
    assert result['published_at'] is None and result['extraction'] == 'main'
    assert 'IGNORE' not in result['content'] and 'Bond risk fixture' in result['content']


def test_private_dns_answer_is_rejected_even_for_allowed_host(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='nonpublic'):
        public_addresses('www.finra.org')


def test_connection_pins_checked_address_and_rejects_redirect_to_metadata(monkeypatch):
    destinations = []
    sni = []

    class Sock:
        def close(self):
            pass

    class Context:
        def wrap_socket(self, sock, server_hostname):
            sni.append(server_hostname)
            return sock

    class Response:
        status = 302

        def getheader(self, name, default=None):
            return 'http://169.254.169.254/latest/meta-data/' if name == 'Location' else default

    class Connection:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr('quant_company.finance_sources.public_addresses', lambda host: ['8.8.8.8'])
    monkeypatch.setattr('socket.create_connection', lambda addr, **kw: destinations.append(addr) or Sock())
    monkeypatch.setattr('ssl.create_default_context', lambda: Context())
    monkeypatch.setattr('http.client.HTTPSConnection', Connection)
    with pytest.raises(ValueError, match='official_https_host_required'):
        download('https://www.finra.org/example')
    assert destinations == [('8.8.8.8', 443)] and sni == ['www.finra.org']


def prepare(company, monkeypatch, downloader=None):
    calls = []

    def fixture(url):
        calls.append(url)
        if downloader:
            return downloader(url)
        return {'url': url, 'html': HTML, 'body_sha256': hashlib.sha256(HTML.encode()).hexdigest(),
                'body_bytes': len(HTML.encode()), 'content_type': 'text/html', 'http_last_modified': None}

    monkeypatch.setattr('quant_company.finance_sources.download', fixture)
    company.roles['director'] = company.roles['director'].model_copy(
        update={'tools': ['finance_search', 'finance_read', 'read_source']})
    request = submit(company, 'official', 'Prepare an official source report')
    turn = turn_for(company, request['task_id'])
    company.prepare_turn(turn)
    response = ProviderResponse(request_id=turn, decision=AgentDecision(say='', status='continue',
        tools=[{'name': 'finance_read', 'arguments': {'document_id': 'finra-bonds'}}]))
    return request, turn, response, calls


def test_fetch_register_cite_read_remainder_and_retry_use_one_immutable_original(company, monkeypatch):
    request, turn, response, calls = prepare(company, monkeypatch)
    company.commit_turn(turn, response)
    assert company.commit_turn(turn, response)['duplicate'] and len(calls) == 1
    state = company.project_state(request['project_id'])
    receipt = json.loads(next(m['text'] for m in state['messages'] if m['kind'] == 'tool'))['receipt']
    assert receipt['source_id'].startswith('finance:') and receipt['jurisdiction'] == 'US'
    assert receipt['published_at'] is None and receipt['next_offset'] == 12000
    with company.db.transaction() as conn:
        assert conn.execute('SELECT original_html FROM finance_fetches').fetchone()['original_html'] == HTML
        remaining = company._tool(conn, request['project_id'], ToolRequest(name='read_source', arguments={
            'source_id': receipt['source_id'], 'offset': receipt['next_offset']}))
        assert remaining['content'] and remaining['offset'] == 12000
        assert remaining['metadata']['body_sha256'] == receipt['body_sha256']
    next_turn = turn_for(company, request['task_id'])
    prompt = company.prepare_turn(next_turn)['request']['prompt']
    assert receipt['source_id'] in prompt
    company.commit_turn(next_turn, ProviderResponse(request_id=next_turn, decision=AgentDecision(
        say='Fixture source inspected', status='complete', artifacts=[{
            'title': 'Fixture evidence', 'content': 'Synthetic transport only', 'source_ids': [receipt['source_id']]}])))
    other = company.ingest(event_key='other', text='Another project', owner='UHUMAN')
    with company.db.transaction() as conn, pytest.raises(PolicyError, match='Unavailable'):
        company._tool(conn, other['project_id'], ToolRequest(name='read_source', arguments={'source_id': receipt['source_id']}))


def test_fetch_failure_returns_receipt_without_citable_source(company, monkeypatch):
    def denied(url):
        raise ValueError('http_403')
    request, turn, response, calls = prepare(company, monkeypatch, denied)
    assert company.commit_turn(turn, response)['state'] == 'completed'
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sources WHERE id LIKE 'finance:%%'").fetchone()['n'] == 0
    receipt = json.loads(next(m['text'] for m in company.project_state(request['project_id'])['messages']
                             if m['kind'] == 'tool'))['receipt']
    assert not receipt['ok'] and receipt['error'] == 'http_403' and 'source_id' not in receipt


def test_http_does_not_hold_project_lock_or_publish_after_stop(company, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(url):
        started.set()
        assert release.wait(5)
        return {'url': url, 'html': HTML, 'body_sha256': hashlib.sha256(HTML.encode()).hexdigest(),
                'body_bytes': len(HTML), 'content_type': 'text/html', 'http_last_modified': None}

    request, turn, response, calls = prepare(company, monkeypatch, slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(company.commit_turn, turn, response)
        assert started.wait(5)
        stopped = pool.submit(submit, company, 'stop', '중단해', request['project_id'], control_action='pause')
        try:
            stopped.result(timeout=2)
        finally:
            release.set()
        assert pending.result(timeout=5)['state'] == 'stale'
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sources WHERE id LIKE 'finance:%%'").fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM finance_fetches').fetchone()['n'] == 1


def test_unauthorized_fetch_is_rejected_before_network(company, monkeypatch):
    request, turn, response, calls = prepare(company, monkeypatch)
    company.roles['director'] = company.roles['director'].model_copy(update={'tools': []})
    with pytest.raises(PolicyError, match='Unauthorized'):
        company.commit_turn(turn, response)
    assert not calls
