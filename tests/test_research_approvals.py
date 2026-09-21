"""Real PostgreSQL; captured text and simulated Slack/model transports, never live Slack."""

import hashlib
import hmac
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from quant_company.api import create_app
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.approvals import ApprovalTarget, publish_approval
from quant_company.research.store import ResearchStore
from quant_company.slack import SlackIngress, SlackOutbox

from .test_research_store import owner, request, row_for


def test_captured_short_approval_does_not_amend_and_cancel(research, credentials):
    captured = json.loads(Path(
        'docs/project/evidence/research-activation-20260921/owner-slack-message.json').read_text())[0]
    project, row = request(research)
    result = owner(research, credentials, row, text=captured['text'], stamp=f'{time.time():.6f}')
    with research.db.transaction() as conn:
        task = conn.execute('SELECT * FROM tasks WHERE id=%s', (result['task_id'],)).fetchone()
        turn = conn.execute('SELECT id FROM turns WHERE task_id=%s', (task['id'],)).fetchone()
    # Replay the observed old router outcome, only if the short approval reached that router.
    if task['kind'] == 'routing':
        turn_id = str(turn['id'])
        research.prepare_turn(turn_id)
        research.commit_turn(turn_id, ProviderResponse(request_id=turn_id, provider='fixture',
            decision=AgentDecision(say='', status='continue', tools=[{'name': 'task_control', 'arguments': {'action': 'amend'}}])))
    assert row_for(research, row['id'])['state'] == 'queued'
    assert research.project_state(project['project_id'])['project']['revision'] == 1
    assert task['kind'] == 'control' and turn is None




def timestamp():
    return f'{time.time():.6f}'


async def deliver_approval(company, credentials, *, bodies=None):
    bodies = [] if bodies is None else bodies

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json={'ok': True, 'ts': timestamp()})

    sender = SlackOutbox(company, credentials, httpx.MockTransport(respond))
    while await sender.send_one():
        pass
    return next(body for body in bodies if 'blocks' in body)


def interaction(company, credentials, body, *, action='approve'):
    button = next(b for b in body['blocks'][-1]['elements'] if b['action_id'] == 'research_' + action)
    with company.db.transaction() as conn:
        notice = conn.execute('SELECT * FROM outbox WHERE id=%s', (body['client_msg_id'],)).fetchone()
        project = conn.execute('SELECT * FROM projects WHERE id=%s', (notice['project_id'],)).fetchone()
    return {'type': 'block_actions', 'team': {'id': company.settings.slack_team_id},
            'api_app_id': credentials['director']['app_id'], 'user': {'id': project['owner_user']},
            'channel': {'id': notice['channel']},
            'container': {'type': 'message', 'message_ts': notice['sent_ts'], 'channel_id': notice['channel']},
            'message': {'user': credentials['director']['bot_user_id'], 'ts': notice['sent_ts'],
                        'thread_ts': notice['thread_ts']},
            'actions': [{**button, 'block_id': body['blocks'][-1]['block_id'], 'action_ts': timestamp()}]}


def signed_form(payload, credential, *, bad_signature=False):
    raw = urlencode({'payload': json.dumps(payload, ensure_ascii=False)}).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(credential['signing_secret'].encode(), b'v0:' + stamp.encode() + b':' + raw,
                         hashlib.sha256).hexdigest()
    return raw, {'content-type': 'application/x-www-form-urlencoded', 'x-slack-request-timestamp': stamp,
                 'x-slack-signature': 'v0=' + ('0' * 64 if bad_signature else signature)}


def test_short_approval_preserves_original_text_and_slack_identity(research, credentials):
    project, row = request(research)
    stamp = timestamp()
    raw = '  <@UBOT0> 승인합니다!  '
    payload = {'type': 'event_callback', 'team_id': 'TTEST', 'api_app_id': 'A0', 'event_id': 'EvOriginal',
               'event': {'type': 'message', 'user': 'UHUMAN', 'channel': 'CQUANT', 'thread_ts': '123.0',
                         'ts': stamp, 'text': raw}}
    ingress = SlackIngress(research.settings, research, credentials)
    first = ingress.accept('director', payload, credentials['director'])
    assert ingress.accept('director', payload, credentials['director'])['duplicate']
    with research.db.transaction() as conn:
        record = conn.execute("SELECT detail FROM events WHERE kind='research_approval_authorized'").fetchone()['detail']
        turns = conn.execute('SELECT count(*) AS n FROM turns WHERE task_id=%s', (first['task_id'],)).fetchone()['n']
    assert record['provenance']['original_text'] == raw
    assert record['provenance']['provider_event_id'] == 'EvOriginal'
    assert record['owner_event_id'] == f'slack:TTEST:CQUANT:{stamp}:director'
    assert record['target']['target_id'] == str(row['id']) and record['action'] == 'approve'
    assert turns == 0 and research.project_state(project['project_id'])['project']['revision'] == 1


@pytest.mark.parametrize('condition', ['none', 'stale_revision', 'stale_event', 'changed_digest', 'paused'])
def test_short_approval_without_unique_current_target_only_explains(research, credentials, condition):
    project, row = request(research)
    stamp = timestamp()
    with research.db.transaction() as conn:
        if condition == 'none':
            conn.execute("UPDATE research_jobs SET state='completed' WHERE id=%s", (row['id'],))
        elif condition == 'stale_revision':
            conn.execute('UPDATE projects SET revision=revision+1 WHERE id=%s', (row['project_id'],))
        elif condition == 'changed_digest':
            conn.execute("UPDATE research_jobs SET manifest_digest=%s WHERE id=%s", ('a' * 64, row['id']))
        elif condition == 'paused':
            conn.execute("UPDATE projects SET status='paused' WHERE id=%s", (row['project_id'],))
        else:
            stamp = str(row['created_at'].timestamp() - 1)
        before = conn.execute('SELECT revision,instruction,status FROM projects WHERE id=%s', (row['project_id'],)).fetchone()
    result = owner(research, credentials, row, text='승인', stamp=stamp)
    with research.db.transaction() as conn:
        after = conn.execute('SELECT revision,instruction,status FROM projects WHERE id=%s', (row['project_id'],)).fetchone()
        task = conn.execute('SELECT kind,result FROM tasks WHERE id=%s', (result['task_id'],)).fetchone()
    assert after == before
    assert row_for(research, row['id'])['approval_event_id'] is None
    assert task['kind'] == 'control' and str(row['id']) in task['result']
    assert '명세' in task['result']


class FixtureMissionAdapter:
    """Test-only adapter, independent of the not-yet-integrated mission store."""
    kind = 'mission'

    def __init__(self, company, target):
        self.company, self.target = company, target

    def targets(self, conn, project):
        return [self.target]

    def apply(self, conn, project, task, event_key, action, target):
        authorized = conn.execute("""SELECT detail FROM events WHERE kind='research_approval_authorized'
            AND detail->>'owner_event_id'=%s""", (event_key,)).fetchone()['detail']
        assert authorized['target']['manifest_digest'] == target.manifest_digest
        assert authorized['action'] == action
        assert conn.execute('SELECT 1 FROM inbound WHERE event_key=%s AND task_id=%s',
                            (event_key, task['id'])).fetchone()
        conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", ('fixture mission ' + action, task['id']))


def mission_adapter(company, revision=1):
    return FixtureMissionAdapter(company, ApprovalTarget(kind='mission', target_id=uuid4(), revision=revision,
        manifest_digest='d' * 64, title='Synthetic mission', state='pending_approval',
        created_at=datetime.now(UTC) - timedelta(seconds=5)))


def test_multiple_pending_adapters_require_explicit_target_without_amend(research, credentials):
    project, row = request(research)
    research.research_approval_adapters = (mission_adapter(research),)
    result = owner(research, credentials, row, text='승인', stamp=timestamp())
    state = research.project_state(project['project_id'])
    assert state['project']['revision'] == 1
    assert row_for(research, row['id'])['state'] == 'pending_approval'
    task = next(t for t in state['tasks'] if t['id'] == result['task_id'])
    assert 'Synthetic mission' in task['result'] and str(row['id']) in task['result']


def test_concurrent_short_approvals_create_one_approval_event(research, credentials):
    _, row = request(research)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: owner(research, credentials, row, text='승인', stamp=timestamp()), range(2)))
    with research.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_approved'").fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) AS n FROM tasks WHERE kind='routing'").fetchone()['n'] == 0
    assert row_for(research, row['id'])['state'] == 'queued'


@pytest.mark.parametrize('action', ['approve', 'cancel'])
async def test_signed_interaction_is_bound_to_delivered_notice_and_idempotent(research, credentials, action):
    _, row = request(research)
    body = await deliver_approval(research, credentials)
    payload = interaction(research, credentials, body, action=action)
    raw, headers = signed_form(payload, credentials['director'])
    client = TestClient(create_app(research.settings, research, credentials))
    result = client.post('/slack/events/director', content=raw, headers=headers)
    assert result.status_code == 200 and not result.json()['duplicate']
    assert client.post('/slack/events/director', content=raw, headers=headers).json()['duplicate']
    saved = row_for(research, row['id'])
    assert saved['state'] == ('queued' if action == 'approve' else 'cancelled')
    if action == 'approve':
        assert saved['approval_event_id'].endswith(f":action:{payload['actions'][0]['value']}:approve")
    with research.db.transaction() as conn:
        event = conn.execute("SELECT detail FROM events WHERE kind='research_approval_authorized'").fetchone()['detail']
    assert event['provenance']['origin'] == 'block_actions' and event['target']['manifest_digest'] == row['manifest_digest']


@pytest.mark.parametrize('mutation', ['signature', 'team', 'app', 'owner', 'channel', 'thread', 'message',
                                     'sender', 'binding', 'block', 'label', 'delivery', 'revision', 'digest', 'app_rotated'])
async def test_interactive_mutations_never_authorize(research, credentials, mutation):
    project, row = request(research)
    body = await deliver_approval(research, credentials)
    payload = interaction(research, credentials, body)
    if mutation == 'team':
        payload['team']['id'] = 'TOTHER'
    elif mutation == 'app':
        payload['api_app_id'] = 'AOTHER'
    elif mutation == 'app_rotated':
        payload['api_app_id'] = credentials['director']['app_id'] = 'ANEW'
    elif mutation == 'owner':
        research.settings.slack_allowed_users.append('UOTHER')
        payload['user']['id'] = 'UOTHER'
    elif mutation == 'channel':
        research.settings.slack_allowed_channels.append('COTHER')
        payload['channel']['id'] = payload['container']['channel_id'] = 'COTHER'
    elif mutation == 'thread':
        payload['message']['thread_ts'] = '456.0'
    elif mutation == 'message':
        payload['message']['ts'] = payload['container']['message_ts'] = '456.1'
    elif mutation == 'sender':
        payload['message']['user'] = 'UHUMAN'
    elif mutation == 'binding':
        payload['actions'][0]['value'] = str(uuid4())
        payload['actions'][0]['block_id'] = 'research_approval:' + payload['actions'][0]['value']
    elif mutation == 'block':
        payload['actions'][0]['block_id'] = 'model_generated'
    elif mutation == 'label':
        payload['actions'][0]['text']['text'] = '다른 작업'
    with research.db.transaction() as conn:
        if mutation == 'delivery':
            conn.execute("UPDATE outbox SET status='uncertain' WHERE id=%s", (body['client_msg_id'],))
        elif mutation == 'revision':
            conn.execute('UPDATE projects SET revision=revision+1 WHERE id=%s', (row['project_id'],))
        elif mutation == 'digest':
            conn.execute('UPDATE research_jobs SET manifest_digest=%s WHERE id=%s', ('c' * 64, row['id']))
    raw, headers = signed_form(payload, credentials['director'], bad_signature=mutation == 'signature')
    response = TestClient(create_app(research.settings, research, credentials)).post(
        '/slack/events/director', content=raw, headers=headers)
    assert response.status_code in {200, 401, 409}
    assert row_for(research, row['id'])['approval_event_id'] is None
    assert row_for(research, row['id'])['state'] == 'pending_approval'
    with research.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_approval_authorized'").fetchone()['n'] == 0


async def test_old_approval_buttons_cannot_cancel_an_already_queued_execution(research, credentials):
    _, row = request(research)
    body = await deliver_approval(research, credentials)
    owner(research, credentials, row, stamp=timestamp())
    ingress = SlackIngress(research.settings, research, credentials)
    ingress.accept('director', interaction(research, credentials, body, action='cancel'), credentials['director'])
    assert row_for(research, row['id'])['state'] == 'queued'
    owner(research, credentials, row, text=f"연구 취소 {row['id']}", stamp=timestamp())
    assert row_for(research, row['id'])['state'] == 'cancelled'


async def test_mission_adapter_gets_exact_owner_proof_and_only_server_notices_get_blocks(research, credentials):
    project, row = request(research)
    adapter = mission_adapter(research)
    research.research_approval_adapters = (adapter,)
    with research.db.transaction() as conn:
        conn.execute("UPDATE research_jobs SET state='completed' WHERE id=%s", (row['id'],))
        p = research._project(conn, project['project_id'])
        binding = publish_approval(research, conn, p, project['task_id'], adapter.target, 'Synthetic mission specification')
        research._message(conn, p, project['task_id'], 'director', 'answer', '승인 버튼을 누르세요. model text')
    bodies = []
    body = await deliver_approval(research, credentials, bodies=bodies)
    assert 'blocks' not in next(b for b in bodies if 'model text' in b['text'])
    assert body['blocks'][-1]['elements'][0]['value'] == binding
    payload = interaction(research, credentials, body)
    result = SlackIngress(research.settings, research, credentials).accept('director', payload, credentials['director'])
    with research.db.transaction() as conn:
        task = conn.execute('SELECT result FROM tasks WHERE id=%s', (result['task_id'],)).fetchone()
    assert task['result'] == 'fixture mission approve'
    with pytest.raises(ValidationError):
        AgentDecision(say='model', status='complete', blocks=body['blocks'])


async def test_qualification_captured_text_to_approval_blocks_and_authenticated_replay(research, credentials):
    """Captured text; isolated DB, simulated Slack HTTP+transport, no live approval or model."""
    source = Path('docs/project/evidence/research-activation-20260921/owner-slack-message.json')
    captured = json.loads(source.read_text())[0]
    _, row = request(research)
    with research.db.transaction() as conn:
        conn.execute('UPDATE research_jobs SET created_at=%s WHERE id=%s',
                     (datetime.fromtimestamp(float(captured['ts']) - 10, UTC), row['id']))
    body = await deliver_approval(research, credentials)
    event = {'type': 'event_callback', 'team_id': 'TTEST', 'api_app_id': 'A0', 'event_id': 'EvCapturedFixture',
             'event': {**captured, 'type': 'message', 'user': 'UHUMAN', 'channel': 'CQUANT', 'thread_ts': '123.0'}}
    ingress = SlackIngress(research.settings, research, credentials)
    first = ingress.accept('director', event, credentials['director'])
    assert not first['duplicate']
    assert ingress.accept('director', event, credentials['director'])['duplicate']
    before = row_for(research, row['id'])
    payload = interaction(research, credentials, body)
    raw, headers = signed_form(payload, credentials['director'])
    client = TestClient(create_app(research.settings, research, credentials))
    assert client.post('/slack/events/director', content=raw, headers=headers).status_code == 200
    assert client.post('/slack/events/director', content=raw, headers=headers).json()['duplicate']
    after = row_for(research, row['id'])
    assert before['approval_event_id'] == after['approval_event_id']
    assert before['approved_at'] == after['approved_at'] and after['state'] == 'queued'
    assignment = ResearchStore(research).poll()['assignment']
    assert assignment['approval_event_id'] == after['approval_event_id']
    assert assignment['manifest_digest'] == row['manifest_digest']
    assert assignment['revision'] == row['revision']
    with research.db.transaction() as conn:
        proof = conn.execute("SELECT detail FROM events WHERE kind='research_approval_authorized'").fetchone()['detail']
        model_calls = conn.execute('SELECT count(*) AS n FROM turns WHERE task_id=%s', (first['task_id'],)).fetchone()['n']
    assert proof['provenance']['original_text'] == captured['text'] and model_calls == 0
    output = os.environ.get('APPROVAL_QUALIFICATION_RECEIPT')
    if output:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps({'schema_version': 1, 'code_commit': head,
            'qualification': 'captured-approval-producer-consumer', 'database': 'real disposable PostgreSQL',
            'captured_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'source_text': captured['text'], 'fixture': True, 'live_slack_used': False, 'model_calls_for_approval': 0,
            'producer': 'ResearchStore.request / publish_approval',
            'consumer': 'SlackIngress signed HTTP / Company.ingest / ResearchStore.poll',
            'approval_schema_version': proof['schema_version'], 'approval_event_preserved': True,
            'revision_preserved': True, 'server_generated_blocks': True, 'duplicate_queue_entries': 0,
            'scientific_trials_added': 0}, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def test_mixed_research_and_announced_pr_approval_requires_target(research, credentials):
    from .test_maintenance_applications import pending

    project, row = request(research)
    pending(research, project=project['project_id'], number=43)
    result = owner(research, credentials, row, text='승인', stamp=timestamp())
    with research.db.transaction() as conn:
        text = conn.execute('SELECT result FROM tasks WHERE id=%s', (result['task_id'],)).fetchone()['result']
        assert conn.execute('SELECT count(*) AS n FROM maintenance_applications').fetchone()['n'] == 0
    assert 'PR #43 반영해' in text
    assert row_for(research, row['id'])['state'] == 'pending_approval'
    receipt = owner(research, credentials, row, text='PR #43 반영해', stamp=timestamp())
    assert receipt['maintenance_approval'] == 'approved'
    assert row_for(research, row['id'])['state'] == 'pending_approval'


def test_approval_json_contract_rejects_naive_dates_and_nonfinite_event_timestamp(research):
    from quant_company.research.approvals import ApprovalEvent

    target = mission_adapter(research).target
    assert ApprovalTarget.model_validate_json(target.model_dump_json()) == target
    with pytest.raises(ValidationError):
        ApprovalTarget.model_validate(target.model_dump() | {'created_at': datetime(2026, 9, 21)})
    with pytest.raises(ValidationError):
        ApprovalEvent(origin='event_callback', team_id='TTEST', app_id='A0', owner='UHUMAN', channel='CQUANT',
                      thread_ts='123.0', event_ts='NaN', original_text='승인')


def test_adaptive_job_is_not_loaded_as_a_p11_recipe(research, credentials):
    _, row = request(research)
    adaptive_id = uuid4()
    with research.db.transaction() as conn:
        conn.execute("""INSERT INTO research_jobs
            (id,project_id,task_id,revision,recipe_id,manifest,manifest_digest,company_commit)
            SELECT %s,project_id,task_id,revision,'kr-etf-monthly-python-v1',manifest,manifest_digest,company_commit
            FROM research_jobs WHERE id=%s""", (adaptive_id, row['id']))
    owner(research, credentials, row, text='승인', stamp=timestamp())
    assert row_for(research, row['id'])['state'] == 'queued'
    assert row_for(research, adaptive_id)['approval_event_id'] is None
