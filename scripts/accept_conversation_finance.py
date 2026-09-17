"""Explicit operator acceptance against a configured live service; never run by CI.

Run inside the API environment with one of: start, steer, status, verify.
Creates one labelled Slack thread in the first configured channel. Owner inputs are
operator-provided acceptance scenarios, not fabricated signed Slack ingress events.
"""

import json
import sys
from datetime import UTC, datetime

import httpx

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.maintenance.policy import SECRET

KEY = 'operator-acceptance:conversation-finance-v1'
SCENARIO = (
    '[운영 인수 테스트] 실제 투자·백테스트가 아닌 자료 조사 협업을 확인합니다. '
    '국내 ETF를 주식·채권으로 구분해 검토할 때 필요한 짧은 메모를 만들어주세요. '
    '총괄은 financial_strategist와 researcher_kr에게 각각 1회 위임하세요. '
    '금융전략은 finance_search로 채권 ETF 후보를 확인하고 finance_read로 finra-bonds 원문을 읽어 '
    '채권 위험과 확인할 항목을 3줄로 정리하세요. 미국 설명을 국내 제도로 적용하지 마세요. '
    '국내연구는 data에게 국내 ETF 데이터 기간·출처를 실제 lake 도구로 확인하도록 1회 직접 위임하고, '
    'finance_read로 krx-etf-trading 원문을 읽어 국내 제도 자료와 연결하세요. '
    '데이터는 전체 스캔 없이 목록과 ETF 자료 하나의 날짜 범위만 확인하세요. '
    '각 담당자는 본인 artifact 1개와 실제 source_id를 남기고 say에도 핵심 내용을 적으세요. '
    '총괄은 자식 두 결과를 기다린 뒤 자신의 artifact와 10줄 이내 최종 답변을 작성하세요. '
    '답변에 원문 URL·자료/조회 시각·확인하지 못한 항목을 포함하세요. 추가 위임·예약은 하지 마세요.'
)
AMENDMENT = '채권 ETF부터 우선 정리해줘. 주식 ETF의 상세 분석은 제외하고, 원문 출처와 데이터 기간 확인은 유지해줘.'


def run(mode):
    company = Company(Settings())
    settings = company.settings
    assert settings.slack_allowed_users and settings.slack_allowed_channels
    credentials = json.loads(settings.slack_credentials_file.read_text())
    token = credentials['director']['bot_token']
    with company.db.transaction() as conn:
        entry = conn.execute('SELECT project_id FROM inbound WHERE event_key=%s', (KEY + ':bootstrap',)).fetchone()
    if mode == 'start':
        if entry:
            return {'already_initialized': True, 'project_id': str(entry['project_id']),
                    'note': 'Never blindly repost an uncertain Slack root.'}
        owner, channel = settings.slack_allowed_users[0], settings.slack_allowed_channels[0]
        initial = company.ingest(event_key=KEY + ':bootstrap', text=SCENARIO, owner=owner, status_only=True)
        project_id = initial['project_id']
        with company.db.transaction() as conn:
            company._event(conn, 'operator_acceptance_root_requested', {
                'new_slack_ingress': False, 'scenario': SCENARIO, 'code_commit': settings.company_code_commit,
                'authorization_quote': '그럼 다음 진행해줘'}, project_id)
        # One attempt. An ambiguous result requires reconciliation, not an automatic repost.
        response = httpx.post('https://slack.com/api/chat.postMessage', timeout=15,
                              headers={'Authorization': 'Bearer ' + token}, json={
                                  'channel': channel, 'client_msg_id': project_id, 'unfurl_links': False,
                                  'text': '[운영 인수 테스트] 대화 제어·공식 금융자료 읽기·직원 협업을 확인합니다. '
                                          '운영자가 승인된 구축 검증을 자동 실행하는 별도 스레드입니다. '
                                          '신규 사용자 요청이나 실제 투자·백테스트 결과가 아닙니다.'})
        response.raise_for_status()
        result = response.json()
        assert result.get('ok'), result.get('error')
        with company.db.transaction() as conn:
            conn.execute('UPDATE projects SET channel=%s,thread_ts=%s WHERE id=%s',
                         (channel, result['ts'], project_id))
            company._event(conn, 'operator_acceptance_root_delivered', {'ts': result['ts'], 'channel': channel}, project_id)
        request = company.ingest(event_key=KEY + ':assignment', text=SCENARIO, owner=owner, project_id=project_id)
        return {'operator_acceptance': True, 'new_slack_ingress': False, 'request': request,
                'channel': channel, 'thread_ts': result['ts']}
    assert entry, 'Start has not run'
    project_id = str(entry['project_id'])
    state = company.project_state(project_id)
    project = state['project']
    if mode == 'steer':
        assert any(t['parent_id'] for t in state['tasks']), 'Wait for the first delegation before steering'
        request = company.ingest(event_key=KEY + ':amend', text=AMENDMENT, owner=project['owner_user'],
                                 project_id=project_id, interpret=True)
        if not request['duplicate']:
            with company.db.transaction() as conn:
                company._message(conn, company._project(conn, project_id), request['task_id'], 'director', 'control',
                                 '[운영 인수: 자동 입력] ' + AMENDMENT)
                company._event(conn, 'operator_acceptance_amendment', {
                    'new_slack_ingress': False, 'text': AMENDMENT}, project_id)
        return request
    with company.db.transaction() as conn:
        sources = conn.execute('''SELECT id,title,uri,available_at,metadata FROM sources
            WHERE project_id=%s AND (id LIKE 'finance:%%' OR id LIKE 'lake:%%')''', (project_id,)).fetchall()
        deliveries = conn.execute('''SELECT id,agent,revision,status,sent_ts,error FROM outbox
            WHERE project_id=%s ORDER BY created_at''', (project_id,)).fetchall()
        calls = conn.execute('''SELECT t.id,t.request->>'model' AS model,t.response->>'provider' AS provider,
            t.response->'usage' AS usage FROM turns t JOIN tasks k ON k.id=t.task_id
            WHERE k.project_id=%s AND t.request IS NOT NULL''', (project_id,)).fetchall()
    output = {'checked_at': datetime.now(UTC).isoformat(), 'code_commit': settings.company_code_commit,
              'operator_acceptance': True, 'new_slack_ingress': False,
              'state': state, 'sources': sources, 'deliveries': deliveries, 'calls': calls}
    if mode == 'verify':
        assert project['revision'] == 2 and AMENDMENT in project['instruction']
        current = [t for t in state['tasks'] if t['revision'] == 2]
        assert current and all(t['status'] == 'completed' for t in current), 'Current work incomplete'
        assert {t['agent'] for t in current} == {'director', 'financial_strategist', 'researcher_kr', 'data'}
        assert any(e['kind'] == 'owner_intent_applied' and e['detail'].get('action') == 'amend' for e in state['events'])
        assert any(t['status'] == 'superseded' for t in state['tasks'] if t['revision'] == 1)
        assert len([a for a in state['artifacts'] if a['revision'] == 2]) >= 4
        assert any(s['id'].startswith('finance:') and s['metadata']['jurisdiction'] == 'KR' for s in sources)
        assert any(s['id'].startswith('finance:') and s['metadata']['jurisdiction'] == 'US' for s in sources)
        assert any(s['id'].startswith('lake:') for s in sources)
        assert all(d['status'] == 'delivered' for d in deliveries if d['revision'] == 2)
        final = next(t['result'] for t in current if t['agent'] == 'director')
        assert '채권' in final and 'http' in final
        readback = httpx.get('https://slack.com/api/conversations.replies', timeout=15,
                            headers={'Authorization': 'Bearer ' + token},
                            params={'channel': project['channel'], 'ts': project['thread_ts'], 'limit': 100}).json()
        assert readback.get('ok') and not readback.get('has_more')
        output['slack_readback'] = [{k: row.get(k) for k in ('ts', 'user', 'bot_id', 'text', 'thread_ts')}
                                    for row in readback['messages']]
        posted = {row['ts'] for row in readback['messages']}
        assert all(d['sent_ts'] in posted for d in deliveries if d['revision'] == 2)
        output['status'] = 'passed'
    else:
        assert mode == 'status'
    return output


if __name__ == '__main__':
    encoded = json.dumps(run(sys.argv[1]), default=str, ensure_ascii=False)
    assert not SECRET.search(encoded), 'Possible secret omitted'
    print(encoded)
