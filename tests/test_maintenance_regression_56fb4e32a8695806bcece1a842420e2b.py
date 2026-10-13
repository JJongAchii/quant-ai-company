import re
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company import system_state
from quant_company.maintenance import cases, requests


FIELDS = {'stage', 'responsible_party', 'next_action', 'automatic_retry_allowed'}
PR_URL = 'https://example.invalid/maintenance/pull/17'
APPLIED_TEXT = '연결된 개선안의 반영이 완료됐습니다.'


@pytest.fixture
def recorded_progress(company, monkeypatch):
    assert all(callable(getattr(requests, name, None))
               for name in ('report', 'status', 'progress_text')), 'Progress consumer must be available'
    owner, channel = 'UPROGRESS_FIXTURE', 'DMAINTENANCE_FIXTURE'
    project = {'id': uuid4(), 'owner_user': owner, 'channel': channel}
    monkeypatch.setattr(company.settings, 'slack_allowed_users', [owner])
    monkeypatch.setattr(company.settings, 'slack_allowed_channels', [channel])
    runtime = {'enabled': True, 'daily_model_call_cap': 1,
               'company_daily_model_call_cap': 1,
               'budget_resets_at': '2030-01-02T00:00:00+00:00'}
    truth = {'disposition': 'reproduced'}
    # Keep the target DB queries and consumers real; isolate unrelated services.
    monkeypatch.setattr(requests, 'service', lambda conn, company, owner: dict(runtime))
    monkeypatch.setattr(cases, 'lookup', lambda conn, project_id: None)
    monkeypatch.setattr(system_state, 'assessment', lambda conn, job_id: dict(truth))
    with company.db.transaction() as conn:
        conn.execute('''CREATE TEMP TABLE maintenance_jobs (
            id uuid PRIMARY KEY, kind text NOT NULL, state text NOT NULL,
            payload jsonb NOT NULL, receipt jsonb NOT NULL, error text,
            created_at timestamptz NOT NULL DEFAULT now()) ON COMMIT DROP''')
        conn.execute('''CREATE TEMP TABLE maintenance_calls (
            id text PRIMARY KEY, job_id uuid NOT NULL, request jsonb NOT NULL,
            response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()) ON COMMIT DROP''')
        yield {'company': company, 'conn': conn, 'project': project,
               'runtime': runtime, 'truth': truth}


def seed(data, state, error=None, extra=None):
    conn, project = data['conn'], data['project']
    request_id, case_id = uuid4(), uuid4()
    review_payload = {'owners': [project['owner_user']],
                      'request_project_id': str(project['id'])}
    finding = {} if state == 'done' else {
        'title': '기록된 개선 후보', 'problem': '기록된 문제',
        'hypothesis': '기록된 미검증 가설', 'evidence_keys': [],
        'evaluation': {'mode': 'regression'}}
    payload = {'owners': [project['owner_user']],
               'request_project_id': str(project['id']),
               'observations': [{'project_id': str(project['id'])}],
               'finding': finding, 'investigation_round': 2, 'patch_attempt': 2,
               'candidate_attempts': [{'attempt': 1}]}
    receipt = {'reason': '기록된 진단 이유', 'outcome': None,
               'required_evidence': ['원 요청', '원 출력', 'runtime 영수증'],
               'review_ids': [], 'ci': {'state': 'pending'}}
    receipt.update(extra or {})
    conn.execute('''INSERT INTO maintenance_jobs(id,kind,state,payload,receipt)
        VALUES (%s,'review','done',%s,%s)''',
                 (request_id, Jsonb(review_payload),
                  Jsonb({'case_id': str(case_id), 'reason': '원 진단 이유'})))
    conn.execute('''INSERT INTO maintenance_jobs(id,kind,state,payload,receipt,error)
        VALUES (%s,'repair',%s,%s,%s,%s)''',
                 (case_id, state, Jsonb(payload), Jsonb(receipt), error))
    for job_id in (request_id, case_id):
        conn.execute('''INSERT INTO maintenance_calls(id,job_id,request,response)
            VALUES (%s,%s,%s,%s)''',
                     (str(uuid4()), job_id, Jsonb({'recorded_input': '원 요청'}),
                      Jsonb({'recorded_output': '원 출력'})))
    job = conn.execute('SELECT * FROM maintenance_jobs WHERE id=%s', (request_id,)).fetchone()
    return job, receipt


def snapshot(conn):
    return (conn.execute('SELECT to_jsonb(j) AS value FROM maintenance_jobs j ORDER BY id').fetchall(),
            conn.execute('SELECT to_jsonb(c) AS value FROM maintenance_calls c ORDER BY id').fetchall())


def observe(data, job):
    conn, project = data['conn'], data['project']
    before = snapshot(conn)
    direct = requests.report(conn, job, project['owner_user'])
    status = requests.status(conn, data['company'], project)
    selected = [item for item in status['requests'] if item['request_id'] == str(job['id'])]
    assert len(selected) == 1, 'Status must consume the recorded review and its linked repair'
    shown = selected[0]
    text = requests.progress_text(shown, data['runtime'])
    legacy = requests.progress_text({key: value for key, value in direct.items() if key not in FIELDS},
                                    data['runtime'])
    assert snapshot(conn) == before, 'Progress reads must preserve original jobs and call receipts'
    assert shown == direct, 'Status must forward the report classification and facts unchanged'
    assert legacy == text, 'Direct callers must receive the same classification and guidance'
    return shown, text


@pytest.mark.parametrize('state,error,extra,stage,party,retry,words', [
    ('triage', None, {}, 'investigation', 'maintainer', True, ('근거 조사', '실제 모델 입력')),
    ('patch', None, {}, 'patch', 'maintainer', True, ('코드 수정', '회귀 시험')),
    ('ci', None, {}, 'ci', 'maintainer', True, ('실제 CI', '실패 피드백')),
    ('pr_open', None, {'pr': {'url': PR_URL}}, 'pr_open', 'owner', False,
     ('정확한 후보 PR', '검토·승인', PR_URL)),
    ('pr_open', None, {}, 'pr_open', 'owner', False, ('PR URL이 없', '검토·승인')),
    ('blocked', 'ci_failed_requires_review', {}, 'operator_reconciliation', 'operator', False,
     ('원 요청', '원 출력', '영수증', '대사', '자동 재호출 금지')),
    ('triage', 'uncertain', {}, 'operator_reconciliation', 'operator', False,
     ('불확실', '대사', '자동 재호출 금지')),
    ('patch', None, {'outcome': 'operator_action_required'}, 'operator_reconciliation', 'operator', False,
     ('대사', '자동 재호출 금지')),
    ('blocked', 'review_reconciliation_required', {}, 'operator_reconciliation', 'operator', False,
     ('독립 검토', '원 CLI 출력', '직원 평가', '대사', '자동 재호출 금지')),
    ('done', None, {}, 'diagnosis_complete', 'maintainer', False,
     ('진단 종료', '점검을 마쳤습니다', '수정 후보 없음')),
    ('applied', None, {}, 'applied', 'operator', False, ('운영 반영 완료', APPLIED_TEXT)),
    ('patch', 'daily_model_budget', {}, 'budget_wait', 'maintainer', True,
     ('예산 초기화', '2030-01-02T00:00:00+00:00', '요청은 보존되어 자동 재개됩니다')),
    ('ci', 'transient_ci_error', {}, 'temporary_wait', 'maintainer', True, ('같은 작업', '일시 대기')),
    ('blocked', 'daily_model_budget', {}, 'operator_reconciliation', 'operator', False,
     ('대사', '자동 재호출 금지')),
])
def test_recorded_report_status_and_text_agree(recorded_progress, state, error, extra, stage, party, retry, words):
    job, receipt = seed(recorded_progress, state, error, extra)
    shown, text = observe(recorded_progress, job)
    assert FIELDS <= shown.keys(), 'Recorded progress must expose stage, owner, action and retry guidance'
    assert shown['stage'] == stage
    assert shown['responsible_party'] == party
    assert shown['automatic_retry_allowed'] is retry
    assert isinstance(shown['next_action'], str) and shown['next_action']
    assert shown['next_action'] in text
    label = {'maintainer': '개선봇', 'owner': '소유자', 'operator': '운영자'}[party]
    assert '담당: ' + label in text
    assert all(word in text for word in words)
    assert shown['state'] == state and shown['error'] == error
    assert shown['request_id'] == str(job['id'])
    assert shown['case_id'] == job['receipt']['case_id']
    assert shown['reason'] == receipt['reason']
    assert shown['outcome'] == receipt['outcome']
    assert shown['required_evidence'] == receipt['required_evidence']
    assert shown['review_ids'] == receipt['review_ids']
    assert shown['pr'] == receipt.get('pr')
    assert shown['validation'] == receipt['ci']
    assert shown['candidate_attempt'] == 2 and shown['prior_attempts'] == 1
    assert (APPLIED_TEXT in text) is (state == 'applied')
    assert ('단계: 운영 반영 완료.' in text) is (state == 'applied')
    if state == 'pr_open' and not receipt.get('pr'):
        assert 'https://' not in text


def test_unlimited_budget_retains_recorded_request_resume_guidance(recorded_progress):
    recorded_progress['runtime'].update(daily_model_call_cap=None, company_daily_model_call_cap=None)
    job, _ = seed(recorded_progress, 'patch', 'daily_model_budget')
    shown, text = observe(recorded_progress, job)
    assert FIELDS <= shown.keys()
    assert shown['stage'] == 'budget_wait'
    assert shown['automatic_retry_allowed'] is True
    assert '일일 한도 해제가 적용됐습니다' in text
    assert '저장된 요청을 최신 근거로 재개할 차례를 기다립니다' in text


@pytest.mark.parametrize('state', ['triage', 'ci', 'pr_open', 'done'])
@pytest.mark.parametrize('application_state', ['approved', 'running', 'applied'])
def test_approval_ci_pr_and_application_are_not_a_recorded_applied_job(recorded_progress, state, application_state):
    application = {'state': application_state}
    recorded_progress['truth']['application'] = application
    job, receipt = seed(recorded_progress, state, extra={
        'ci': {'state': 'passed'}, 'pr': {'url': PR_URL},
        'approval': {'state': 'approved'}, 'application': application})
    shown, text = observe(recorded_progress, job)
    assert FIELDS <= shown.keys()
    assert shown['state'] == state and shown['stage'] != 'applied'
    assert shown['pr'] == receipt['pr'] and shown['validation'] == receipt['ci']
    assert shown['assessment']['application'] == application
    assert APPLIED_TEXT not in text and '단계: 운영 반영 완료.' not in text
    if state == 'done':
        assert shown['stage'] == 'diagnosis_complete'
        assert '수리·PR·운영 반영 기록은 없습니다' not in text


@pytest.mark.parametrize('disposition', ['invalidated', 'resolved'])
def test_historical_disposition_does_not_invent_application(recorded_progress, disposition):
    recorded_progress['truth']['disposition'] = disposition
    job, _ = seed(recorded_progress, 'pr_open', extra={'pr': {'url': PR_URL}})
    shown, text = observe(recorded_progress, job)
    assert FIELDS <= shown.keys()
    assert shown['assessment']['disposition'] == disposition
    assert shown['finding_is_current_fact'] is False
    assert 'problem' not in shown and 'hypothesis' not in shown
    assert shown['historical_finding'] == {'problem': '기록된 문제', 'hypothesis': '기록된 미검증 가설'}
    assert shown['stage'] == 'pr_open' and shown['responsible_party'] == 'owner'
    assert APPLIED_TEXT not in text


@pytest.mark.parametrize('fault,error', [
    ('foreign_owner', 'case_owner_scope_mismatch'),
    ('missing', 'case_owner_scope_mismatch'),
    ('cycle', 'case_link_requires_review'),
])
def test_invalid_links_cannot_report_an_applied_job(recorded_progress, fault, error):
    conn = recorded_progress['conn']
    job, receipt = seed(recorded_progress, 'applied')
    case_id = job['receipt']['case_id']
    if fault == 'foreign_owner':
        conn.execute('''UPDATE maintenance_jobs SET payload=jsonb_set(payload,'{owners}',%s)
            WHERE id=%s''', (Jsonb(['UOTHER_FIXTURE']), case_id))
    elif fault == 'missing':
        conn.execute('DELETE FROM maintenance_jobs WHERE id=%s', (case_id,))
    else:
        conn.execute('UPDATE maintenance_jobs SET receipt=%s WHERE id=%s',
                     (Jsonb({**receipt, 'recheck_id': str(job['id'])}), case_id))
    shown, text = observe(recorded_progress, job)
    assert FIELDS <= shown.keys()
    assert shown['state'] == 'blocked' and shown['error'] == error
    assert shown['stage'] == 'operator_reconciliation'
    assert shown['responsible_party'] == 'operator'
    assert shown['automatic_retry_allowed'] is False
    assert '대사' in text and '자동 재호출 금지' in text
    assert APPLIED_TEXT not in text


def test_secret_filter_redacts_before_reporting_completion(recorded_progress, monkeypatch):
    # A harmless marker exercises the filter boundary without putting a secret in a fixture.
    monkeypatch.setattr(requests, 'SECRET', re.compile('redaction_fixture_marker'))
    job, _ = seed(recorded_progress, 'applied', extra={'reason': 'redaction_fixture_marker'})
    shown, text = observe(recorded_progress, job)
    assert FIELDS <= shown.keys()
    assert shown['state'] == 'blocked' and shown['error'] == 'report_contains_possible_secret'
    assert shown['responsible_party'] == 'operator'
    assert shown['automatic_retry_allowed'] is False
    assert 'reason' not in shown and 'case_id' not in shown
    assert 'redaction_fixture_marker' not in text and APPLIED_TEXT not in text


def test_absent_records_remain_absent(recorded_progress):
    conn = recorded_progress['conn']
    before = snapshot(conn)
    shown = requests.status(conn, recorded_progress['company'], recorded_progress['project'])
    assert shown['requests'] == [], 'No recorded request must remain no recorded request'
    assert snapshot(conn) == before
