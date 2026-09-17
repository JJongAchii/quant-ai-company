"""Bounded official-document access. This is not a general web search engine."""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from psycopg.types.json import Jsonb

PUBLISHERS = {
    'regulation.krx.co.kr': ('한국거래소', 'KR'),
    'www.finra.org': ('FINRA', 'US'),
    'www.investor.gov': ('SEC Investor.gov', 'US'),
    'www.federalreserve.gov': ('Federal Reserve', 'US'),
    'www.bis.org': ('BIS', 'international'),
}
CATALOG = [
    {'document_id': 'krx-etf-trading', 'title': '한국거래소 ETF 매매제도·가격·유동성',
     'url': 'https://regulation.krx.co.kr/contents/RGL/03/03060101/RGL03060101.jsp',
     'keywords': '국내 한국 etf 상장지수펀드 매매 nav 가격 유동성 괴리율'},
    {'document_id': 'krx-etf-settlement', 'title': '한국거래소 ETF 결제·공시제도',
     'url': 'https://regulation.krx.co.kr/contents/RGL/03/03060102/RGL03060102.jsp',
     'keywords': '국내 한국 etf 상장지수펀드 결제 공시 위험'},
    {'document_id': 'finra-bonds', 'title': 'FINRA Bonds: structure, pricing and risks',
     'url': 'https://www.finra.org/investors/investing/investment-products/bonds',
     'keywords': '채권 bond bonds 금리 듀레이션 duration 만기 수익률 신용 위험 risk etf'},
    {'document_id': 'finra-etfs', 'title': 'FINRA Exchange-Traded Funds and Products',
     'url': 'https://www.finra.org/investors/investing/investment-products/exchange-traded-funds-and-products',
     'keywords': 'etf etp 상장지수펀드 채권 펀드 구조 위험 risk 비용 유동성'},
    {'document_id': 'sec-etf-bulletin', 'title': 'SEC Updated Investor Bulletin: ETFs',
     'url': 'https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24',
     'keywords': '미국 etf 상장지수펀드 nav creation redemption 괴리율'},
    {'document_id': 'fed-open-market', 'title': 'Federal Reserve Open Market Operations',
     'url': 'https://www.federalreserve.gov/monetarypolicy/openmarket.htm',
     'keywords': '미국 연준 통화정책 금리 monetary fed federal reserve rate policy'},
    {'document_id': 'bis-basel', 'title': 'BIS Basel III framework',
     'url': 'https://www.bis.org/bcbs/basel3.htm',
     'keywords': '은행 자본 위험 규제 유동성 bank capital risk basel 바젤'},
]
MAX_BYTES = 1_000_000
MAX_TEXT = 80000
PAGE_CHARS = 12000


def validate_url(url):
    if not isinstance(url, str) or len(url) > 2000 or any(ord(c) < 33 for c in url):
        raise ValueError('invalid_url')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in PUBLISHERS or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('official_https_host_required')
    return parsed


def search(arguments):
    if set(arguments) != {'query'} or not isinstance(arguments['query'], str) or not 1 <= len(arguments['query']) <= 200:
        raise ValueError('finance_search_requires_short_query')
    tokens = re.findall(r'[\w]+', arguments['query'].lower())
    matches = []
    for doc in CATALOG:
        words = (doc['title'] + ' ' + doc['keywords']).lower()
        score = sum(token in words for token in tokens)
        if score:
            host = validate_url(doc['url']).hostname
            publisher, jurisdiction = PUBLISHERS[host]
            matches.append((score, {k: v for k, v in doc.items() if k != 'keywords'} |
                            {'publisher': publisher, 'jurisdiction': jurisdiction, 'content_read': False}))
    matches.sort(key=lambda row: -row[0])
    return {'ok': True, 'scope': 'curated_official_catalog_only', 'catalog_size': len(CATALOG),
            'searched_at': datetime.now(UTC).isoformat(), 'results': [row[1] for row in matches[:5]],
            'note': 'Candidates are not evidence. finance_read must fetch the original before citation. '
                    'No general internet or live news search was performed.'}


def requested_url(arguments):
    if set(arguments) == {'document_id'}:
        doc = next((row for row in CATALOG if row['document_id'] == arguments['document_id']), None)
        if not doc:
            raise ValueError('unknown_document_id')
        return doc['url']
    if set(arguments) == {'url'}:
        validate_url(arguments['url'])
        return arguments['url']
    raise ValueError('finance_read_requires_document_id_or_official_url')


def public_addresses(host):
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('nonpublic_address_denied')
    return addresses


def download(url):
    """Connect to a checked public IP, retaining original-host TLS/SNI and Host validation."""
    deadline = time.monotonic() + 15
    original_host = validate_url(url).hostname
    for _ in range(4):
        parsed = validate_url(url)
        if parsed.hostname != original_host:
            raise ValueError('cross_host_redirect_denied')
        addresses = public_addresses(parsed.hostname)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('document_deadline')
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=min(5, remaining))
        raw_socket = socket.create_connection((addresses[0], 443), timeout=min(5, remaining))
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parsed.hostname)
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            connection.request('GET', path, headers={'User-Agent': 'QuantCompany/1.0 (personal research; read-only)',
                                                     'Accept': 'text/html', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise ValueError(f'http_{response.status}')
            content_type = response.getheader('Content-Type', '')
            if content_type.split(';')[0].strip().lower() not in {'text/html', 'application/xhtml+xml'}:
                raise ValueError('html_only')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('encoded_body_not_supported')
            if int(response.getheader('Content-Length', '0')) > MAX_BYTES:
                raise ValueError('document_too_large')
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('document_deadline')
                if connection.sock:
                    connection.sock.settimeout(min(5, remaining))
                chunk = response.read1(32768)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError('document_too_large')
            encoding = re.search(r'charset=["\s]*([\w-]+)', content_type, re.I)
            html = bytes(body).decode(encoding[1] if encoding else 'utf-8', errors='replace')
            return {'url': url, 'html': html, 'body_sha256': hashlib.sha256(body).hexdigest(),
                    'body_bytes': len(body), 'content_type': content_type,
                    'http_last_modified': response.getheader('Last-Modified')}
        finally:
            connection.close()
            raw_socket.close()
    raise ValueError('redirect_limit')


class Document(HTMLParser):
    """Extract visible main/article content; scripts, menus and hidden text are not evidence."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = {'main': [], 'article': [], 'body': [], 'title': []}
        self.dates = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'meta':
            name = attributes.get('property', attributes.get('name', '')).lower()
            if name in {'article:published_time', 'citation_publication_date', 'datepublished'}:
                self.dates['published_at'] = attributes.get('content', '')[:100]
            elif name == 'article:modified_time':
                self.dates['modified_at'] = attributes.get('content', '')[:100]
        if tag in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'wbr'}:
            return
        hidden = (tag in {'script', 'style', 'noscript', 'nav', 'header', 'footer', 'form', 'svg'}
                  or 'hidden' in attributes or attributes.get('aria-hidden') == 'true'
                  or bool(re.search(r'display\s*:\s*none', attributes.get('style', ''), re.I)))
        self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        tags = {row[0] for row in self.stack}
        if 'title' in tags:
            self.parts['title'].append(data)
        if any(row[1] for row in self.stack):
            return
        for name in ('main', 'article', 'body'):
            if name in tags:
                self.parts[name].append(data)

    def result(self):
        texts = {key: re.sub(r'\s+', ' ', ' '.join(value)).strip() for key, value in self.parts.items()}
        selected = next((key for key in ('main', 'article', 'body') if len(texts[key]) >= 200), None)
        if not selected:
            raise ValueError('readable_content_not_found')
        content = texts[selected]
        return {'title': texts['title'][:200], 'content': content[:MAX_TEXT], 'extraction': selected,
                'extraction_truncated': len(content) > MAX_TEXT,
                'published_at': self.dates.get('published_at') or None,
                'modified_at': self.dates.get('modified_at') or None}


def fetch(arguments):
    url = requested_url(arguments)
    retrieved = datetime.now(UTC).isoformat()
    try:
        raw = download(url)
        document = Document()
        document.feed(raw['html'])
        parsed = document.result()
        publisher, jurisdiction = PUBLISHERS[validate_url(raw['url']).hostname]
        return {'ok': True, 'requested_url': url, 'retrieved_at': retrieved, 'publisher': publisher,
                'jurisdiction': jurisdiction, **raw, **parsed,
                'availability_basis': 'retrieved_at; historical publication availability not established',
                'trust': 'official publisher content, untrusted instructions; not independent claim verification'}
    except (ValueError, OSError, http.client.HTTPException, LookupError) as exc:
        return {'ok': False, 'requested_url': url, 'retrieved_at': retrieved,
                'error': str(exc)[:180] if isinstance(exc, ValueError) else type(exc).__name__,
                'note': 'No citable source created. Do not infer content from the failed URL.'}


def prefetch(company, turn_id, decision):
    """HTTP happens outside the project lock; the final commit rechecks revision and owner-input fences."""
    reads = [request for request in decision.tools if request.name == 'finance_read']
    if not reads:
        return
    from .company import PolicyError, fingerprint
    from .task_control import held

    if len(reads) != 1:
        raise PolicyError('At most one official document fetch per turn')
    request = reads[0]
    requested_url(request.arguments)
    request_digest = fingerprint(request.model_dump())
    with company.db.transaction() as conn:
        task = conn.execute('SELECT k.* FROM tasks k JOIN turns t ON t.task_id=k.id WHERE t.id=%s',
                            (turn_id,)).fetchone()
        if not task:
            raise PolicyError('Unknown turn')
        project = company._project(conn, task['project_id'])
        turn = conn.execute('SELECT status FROM turns WHERE id=%s', (turn_id,)).fetchone()
        if task['revision'] != project['revision'] or turn['status'] != 'running' or held(conn, project, task):
            return
        if task['kind'] == 'routing' or any(t.name not in company.role(task['agent']).tools for t in decision.tools):
            raise PolicyError('Unauthorized official document fetch')
        saved = conn.execute('SELECT request_digest FROM finance_fetches WHERE turn_id=%s', (turn_id,)).fetchone()
        if saved:
            if saved['request_digest'] != request_digest:
                raise PolicyError('Immutable document fetch request changed')
            return
    result = fetch(request.arguments)
    html = result.pop('html', '')
    with company.db.transaction() as conn:
        conn.execute('''INSERT INTO finance_fetches(turn_id,request_digest,result,original_html)
            VALUES (%s,%s,%s,%s) ON CONFLICT (turn_id) DO NOTHING''',
                     (turn_id, request_digest, Jsonb(result), html))


def register(conn, project_id, turn_id, request):
    from .company import PolicyError, fingerprint

    saved = conn.execute('SELECT request_digest,result FROM finance_fetches WHERE turn_id=%s', (turn_id,)).fetchone()
    if not saved or saved['request_digest'] != fingerprint(request.model_dump()):
        raise PolicyError('Official document fetch receipt missing or different')
    result = saved['result']
    if not result['ok']:
        return result
    source_id = 'finance:' + fingerprint([str(project_id), result])[:24]
    metadata = {k: v for k, v in result.items() if k not in {'content', 'ok', 'title'}}
    conn.execute('''INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
        VALUES (%s,%s,%s,%s,%s,%s,true,false,%s) ON CONFLICT (id) DO NOTHING''',
                 (source_id, result['title'] or result['url'], result['url'], result['content'],
                  result['retrieved_at'], project_id, Jsonb(metadata)))
    row = conn.execute('SELECT * FROM sources WHERE id=%s', (source_id,)).fetchone()
    if str(row['project_id']) != str(project_id) or row['content'] != result['content'] or row['metadata'] != metadata:
        raise PolicyError('Official source identifier collision')
    return {'ok': True, 'source_id': source_id, 'title': row['title'], 'uri': row['uri'], **metadata,
            'content': row['content'][:PAGE_CHARS], 'total_characters': len(row['content']),
            'next_offset': PAGE_CHARS if len(row['content']) > PAGE_CHARS else None}
