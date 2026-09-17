"""Explicit live operator probe in an existing acceptance thread. Never run by CI.

Usage inside the API environment: python scripts/accept_director_mentions.py start|verify PROJECT_ID
One bounded calculation produces real Codex progress and a final Slack answer.
This is operator-supplied input, not a signed human Slack event. No blind reposts.
"""

import json
import sys
from datetime import UTC, datetime

import httpx

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.maintenance.policy import SECRET
from quant_company.system_state import record_verification

KEY = 'operator-acceptance:director-mentions-v1'
SCENARIO = (
    '[운영자 알림 기능 검증: 자동 테스트 입력] calculate 도구로 7+5를 한 번 계산해 주세요. '
    '도구를 호출하는 첫 턴의 say에는 운영 검증 계산을 확인 중이라고 한 줄 안내하세요. '
    '계산 결과를 받은 뒤에는 도구·위임 없이 최종 답변을 완료하세요. '
    '최종 답변에는 운영 검증 완료, 계산 결과, 총괄의 최종 결과·확인 요청에는 요청자를 태그하고 '
    '중간 진행에는 태그하지 않는다는 안내를 간결하게 포함하세요. 금융 연구는 요청하지 않습니다.'
)


def run(mode, project_id):
    company = Company(Settings())
    with company.db.transaction() as conn:
        project = company._project(conn, project_id)
        assert conn.execute("""SELECT 1 FROM events WHERE project_id=%s
            AND kind='operator_acceptance_root_delivered'""", (project_id,)).fetchone(), 'Use an acceptance thread'
        assert project['owner_user'] in company.settings.slack_allowed_users
        assert project['channel'] in company.settings.slack_allowed_channels
    if mode == 'start':
        request = company.ingest(event_key=KEY, text=SCENARIO, owner=project['owner_user'], project_id=project_id)
        if not request['duplicate']:
            with company.db.transaction() as conn:
                company._event(conn, 'operator_director_mentions_probe', {
                    'new_slack_ingress': False, 'task_id': request['task_id'], 'scenario': SCENARIO,
                    'code_commit': company.settings.company_code_commit,
                    'authorization_quote': '총괄 BOT 은 최종적인 결론이나 결과가 나왔을때나 나의 확인이 필요할 때 나를 태그해서 답변하도록 해줘.'
                }, project_id)
        return {'operator_acceptance': True, 'new_slack_ingress': False, 'request': request}
    assert mode == 'verify'
    with company.db.transaction() as conn:
        task = conn.execute('''SELECT k.* FROM tasks k JOIN inbound i ON i.task_id=k.id
            WHERE i.event_key=%s AND k.project_id=%s''', (KEY, project_id)).fetchone()
        assert task and task['status'] == 'completed', 'Probe has not completed; do not resubmit'
        turns = conn.execute('SELECT id,status,response FROM turns WHERE task_id=%s ORDER BY sequence',
                             (task['id'],)).fetchall()
        deliveries = conn.execute('''SELECT o.id,o.text,o.status,o.sent_ts FROM outbox o
            JOIN messages m ON m.id=o.id WHERE m.task_id=%s ORDER BY o.created_at,o.id''', (task['id'],)).fetchall()
        tool = conn.execute("SELECT text FROM messages WHERE task_id=%s AND author='tool:calculate'",
                            (task['id'],)).fetchone()
    assert tool and len(turns) == 2 and len(deliveries) == 2
    assert all(t['status'] == 'completed' and t['response']['provider'] == 'codex' for t in turns)
    assert turns[0]['response']['decision']['status'] == 'continue'
    assert turns[1]['response']['decision']['status'] == 'complete'
    assert all(d['status'] == 'delivered' for d in deliveries), 'Wait for delivery; never blindly repost'
    mention = '<@' + project['owner_user'] + '>'
    assert '<@' not in deliveries[0]['text'] and deliveries[1]['text'].count(mention) == 1
    assert '12' in deliveries[1]['text']
    credentials = json.loads(company.settings.slack_credentials_file.read_text())
    result = httpx.get('https://slack.com/api/conversations.replies', timeout=15,
                      headers={'Authorization': 'Bearer ' + credentials['director']['bot_token']},
                      params={'channel': project['channel'], 'ts': project['thread_ts'], 'limit': 100}).json()
    assert result.get('ok') and not result.get('has_more'), 'Readback needs reconciliation'
    readback = {row['ts']: row for row in result['messages']}
    for delivery in deliveries:
        row = readback[delivery['sent_ts']]
        assert row['user'] == credentials['director']['bot_user_id']
        assert row['text'] == f"[지시 v{project['revision']}] {delivery['text']}"
    evidence = as_json({
        'status': 'passed', 'checked_at': datetime.now(UTC), 'code_commit': company.settings.company_code_commit,
        'operator_acceptance': True, 'new_slack_ingress': False, 'scenario': SCENARIO,
        'project_id': project_id, 'task_id': task['id'], 'channel': project['channel'], 'thread_ts': project['thread_ts'],
        'turns': turns, 'tool_receipt': json.loads(tool['text']), 'deliveries': deliveries,
        'slack_readback': [{k: readback[d['sent_ts']].get(k) for k in ('ts', 'user', 'text')} for d in deliveries],
        'scope': 'Real Codex calculation progress and final answer; owner mention verified by Slack readback. '
                 'Clarification, blockers and maintenance notifications covered by PostgreSQL regression tests. '
                 'No device push notification or read acknowledgement verified.'})
    with company.db.transaction() as conn:
        record_verification(conn, company, identity=KEY, feature='director_owner_mentions',
                            scope=evidence['scope'], evidence=evidence)
    return evidence


if __name__ == '__main__':
    output = json.dumps(run(sys.argv[1], sys.argv[2]), ensure_ascii=False)
    assert not SECRET.search(output), 'Possible secret omitted'
    print(output)
