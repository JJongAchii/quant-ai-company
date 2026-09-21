"""Real database/ingress tests; model and GitHub outputs are explicitly fixtures."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from quant_company.api import create_app
from quant_company.company import Company, PolicyError
from quant_company.contracts import ProviderRequest
from quant_company.maintenance.github import GitHub, blob_sha
from quant_company.maintenance.policy import Triage, digest, writable
from quant_company.maintenance.requests import report
from quant_company.maintenance.runner import proposal_material
from quant_company.owner_controls import effective_limits, parse_daily_limit_command
from quant_company.system_state import assess, current_system, readable, repository_read

from .conftest import queued_turns
from .test_maintenance import SOURCE, ModelFixture, config, make_maintainer
from .test_slack import event, signed


def test_history_and_current_code_share_the_real_provider_input_budget_without_mutating_evidence():
    # Production shape: ~36k history + ~25k system facts + ~58k source excerpts exceeded 90k.
    payload = {'observations': [{'key': 'message:original', 'text': 'Keep the explicit owner request'}],
               'history': {'evidence': [{'key': f'message:{i}', 'text': 'x'*1800,
                                         'created_at': str(i)} for i in range(20)]},
               'current_implementation': {'system': {'facts': 'y'*25000},
                                          'source_files': [{'key': f'code:fixed:{i}', 'blob': 'a'*40,
                                                            'content': 'z'*5800} for i in range(10)]}}
    before = digest(payload)
    material, prompt = proposal_material(payload, Triage)
    ProviderRequest(request_id='bounded-production-shape', model='fixture', prompt=prompt)
    assert len(prompt) <= 88000 and len(json.dumps(payload)) > 90000
    assert material['prompt_excerpted'] and digest(payload) == before
    assert material['observations'] == payload['observations']
    assert material['current_implementation']['system'] == payload['current_implementation']['system']
    assert any(r.get('excerpted') for r in material['current_implementation']['source_files'])


def test_large_system_records_are_compacted_inside_the_same_provider_budget():
    system = {
        'runtime': {'code_commit': 'a'*40, 'config_digest': 'b'*64,
                    'configuration': {'context': 'r'*12000}},
        'repository': {'state': 'observed', 'commit': 'c'*40,
                       'pull_requests': [{'title': 'p'*2000} for _ in range(15)],
                       'ci': [{'url': 'u'*2000} for _ in range(15)]},
        'verifications': [{'id': str(i), 'evidence': {'detail': 'v'*5000}} for i in range(12)],
        'maintenance_jobs': {'records': [{'error': 'e'*2000} for _ in range(10)]},
    }
    payload = {'observations': [{'key': 'message:original', 'text': 'owner request'}],
               'history': {'evidence': [{'key': f'message:{i}', 'text': 'h'*1800,
                                         'created_at': str(i)} for i in range(12)]},
               'current_implementation': {'system': system,
                                          'source_files': [{'key': f'code:fixed:{i}', 'content': 's'*6000}
                                                           for i in range(11)]}}
    before = digest(payload)
    material, prompt = proposal_material(payload, Triage)
    assert len(prompt) <= 88000 and digest(payload) == before
    assert material['prompt_system_compaction'] >= 1
    assert material['current_implementation']['system']['runtime']['code_commit'] == 'a'*40
    assert len(material['current_implementation']['system']['verifications']) <= 5


@pytest.mark.integration
async def test_explicit_current_citation_in_single_artifact_is_carried_into_finding_without_rewriting_response(company):
    class EnvelopeCitation(ModelFixture):
        async def run(self, request):
            response = await super().run(request)
            artifact = response.decision.artifacts[0]
            value = json.loads(artifact.content)
            current = value['finding']['evidence_keys'].pop()
            artifact.content = json.dumps(value)
            artifact.source_ids = [current]
            return response
    runner = make_maintainer(company)
    runner.provider = EnvelopeCitation()
    assert (await runner.tick())['state'] == 'advanced'
    with company.db.transaction() as conn:
        case = conn.execute("SELECT payload FROM maintenance_jobs WHERE kind='repair'").fetchone()['payload']
        saved = conn.execute('SELECT response FROM maintenance_calls').fetchone()['response']
    current = saved['decision']['artifacts'][0]['source_ids'][0]
    assert current in case['finding']['evidence_keys']
    assert case['citation_normalization']['added'] == [current]
    assert case['citation_normalization']['original_response_digest'] == digest(saved)
    assert current not in json.loads(saved['decision']['artifacts'][0]['content'])['finding']['evidence_keys']


@pytest.mark.integration
async def test_current_job_state_supersedes_old_status_messages_in_shared_evidence(company):
    runner = make_maintainer(company)
    await runner.tick()
    with company.db.transaction() as conn:
        case = conn.execute("SELECT id FROM maintenance_jobs WHERE kind='repair'").fetchone()
        conn.execute("UPDATE maintenance_jobs SET error='daily_model_budget' WHERE id=%s", (case['id'],))
        before = current_system(conn, company, ['UHUMAN'])['maintenance_jobs']
        assert any(r['id'] == str(case['id']) and r['error'] == 'daily_model_budget' for r in before['records'])
        conn.execute("UPDATE maintenance_jobs SET error=NULL WHERE id=%s", (case['id'],))
        after = current_system(conn, company, ['UHUMAN'])['maintenance_jobs']
        assert any(r['id'] == str(case['id']) and r['error'] is None for r in after['records'])
        assert 'Historical tool messages are not current state' in after['scope']
        assert current_system(conn, company, ['UOTHER'])['maintenance_jobs']['records'] == []


@pytest.mark.parametrize("text", ['"전체 한도 해제"', '> 전체 한도 해제', '전체 한도 해제?',
                                  '개선BOT 한도 해제 가능해?', '회사 공통 한도 없애도 돼?', '한도 없애줘'])
def test_questions_quotes_and_ambiguous_scopes_are_not_commands(text):
    assert parse_daily_limit_command(text) is None


@pytest.mark.integration
def test_signed_owner_command_works_with_exhausted_quota_once_and_survives_restart(company, credentials):
    runner = make_maintainer(company)
    company.settings.company_max_daily_turns = 100
    runner.config.max_daily_calls = 6
    runner.store.heartbeat()
    with company.db.transaction() as conn:
        conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,1000)")
        conn.execute("UPDATE runtime_control SET paused_until=now()+interval '1 hour',reason='quota'")
    client = TestClient(create_app(company.settings, company, credentials))
    raw, headers = signed(event(credentials, text="<@UBOT0> 전체 일일 한도 해제"), credentials["director"])
    result = client.post('/slack/events/director', content=raw, headers=headers).json()
    assert result["owner_control"]
    assert client.post('/slack/events/director', content=raw, headers=headers).json()["duplicate"]
    assert not company.project_state(result["project_id"])["turns"]
    with company.db.transaction() as conn:
        receipt = conn.execute("SELECT receipt FROM policy_commands").fetchone()["receipt"]
        assert receipt["before"] == {"revision": 0, "company": 100, "maintenance": 6}
        assert effective_limits(conn, Company(company.settings, company.roles), 6) == receipt["after"] == {
            "revision": 1, "company": None, "maintenance": None}
        assert conn.execute("SELECT paused_until>now() AS paused FROM runtime_control").fetchone()["paused"]
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE text LIKE '일일 호출 정책%%'").fetchone()["n"] == 1


@pytest.mark.integration
def test_concurrent_controls_merge_both_scopes_without_lost_update(company):
    make_maintainer(company)
    texts = ['회사 공통 100회 한도 해제', '개선BOT 한도 해제']
    def apply(item):
        index, text = item
        return company.ingest(event_key=f'control-{index}', text=text, owner='UHUMAN', channel='CQUANT',
                              thread_ts=f'control-{index}', daily_limit_command=parse_daily_limit_command(text))
    with ThreadPoolExecutor(2) as executor:
        list(executor.map(apply, enumerate(texts)))
    with company.db.transaction() as conn:
        assert effective_limits(conn, company, 6) == {"revision": 2, "company": None, "maintenance": None}
        assert conn.execute("SELECT count(*) AS n FROM policy_commands").fetchone()["n"] == 2


@pytest.mark.integration
def test_redelivery_of_pre_upgrade_conversation_never_reinterprets_it_as_a_new_control(company):
    make_maintainer(company)
    text = '전체 일일 한도 해제'
    args = dict(event_key='historical-event', text=text, owner='UHUMAN', channel='CQUANT', thread_ts='historical')
    original = company.ingest(**args)
    duplicate = company.ingest(**args, daily_limit_command=parse_daily_limit_command(text))
    assert duplicate['duplicate'] and duplicate['task_id'] == original['task_id']
    with company.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM policy_commands').fetchone()['n'] == 0


@pytest.mark.integration
@pytest.mark.parametrize('overrides', [{"user": "UOTHER"}, {"channel": "COTHER"}, {"bot_id": "BBOT"}])
def test_unauthorized_slack_cannot_change_policy(company, credentials, overrides):
    make_maintainer(company)
    client = TestClient(create_app(company.settings, company, credentials))
    raw, headers = signed(event(credentials, text="<@UBOT0> 전체 일일 한도 해제", **overrides), credentials['director'])
    assert client.post('/slack/events/director', content=raw, headers=headers).json()['ignored']
    with company.db.transaction() as conn:
        assert effective_limits(conn, company)["revision"] == 0


@pytest.mark.integration
def test_compound_control_tracks_queued_diagnosis_separately(company):
    make_maintainer(company)
    text = '개선 BOT 한도 해제하고 다른 스레드의 개선점을 확인해줘'
    result = company.ingest(event_key='compound', text=text, owner='UHUMAN', channel='CQUANT', thread_ts='compound',
                            daily_limit_command=parse_daily_limit_command(text))
    with company.db.transaction() as conn:
        receipt = conn.execute("SELECT receipt FROM policy_commands").fetchone()['receipt']
        assert receipt['state'] == 'applied' and receipt['diagnosis']['accepted']
        assert conn.execute("SELECT state FROM maintenance_jobs WHERE id=%s", (receipt['diagnosis']['request_id'],)).fetchone()['state'] == 'review'
        assert conn.execute("SELECT status FROM tasks WHERE id=%s", (result['task_id'],)).fetchone()['status'] == 'completed'
        assert not queued_turns(company, result['project_id'])


@pytest.mark.integration
async def test_no_service_daily_quota_but_subscription_pause_and_task_limits_remain(company):
    runner = make_maintainer(company)
    with company.db.transaction() as conn:
        conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,10000)")
    assert (await runner.tick())['state'] == 'advanced'
    assert len(runner.provider.requests) == 1  # Maintenance can pass the old 6/100 gate.
    request = company.ingest(event_key='after-100', text='Direct answer', owner='UHUMAN')
    turn = queued_turns(company, request['project_id'])[0]
    assert company.prepare_turn(turn)['state'] == 'ready'
    with company.db.transaction() as conn:
        conn.execute("UPDATE runtime_control SET paused_until=now()+interval '1 hour',reason='quota'")
    assert company.prepare_turn(turn)['state'] == 'defer'
    assert company.settings.company_max_task_turns == 8 and company.settings.company_max_depth == 3


def test_repository_read_boundary_is_broader_than_patch_boundary_and_checks_exact_blobs():
    path = 'src/quant_company/config.py'
    assert readable(path) and not writable(path)
    for forbidden in ['../config.py', '/etc/passwd', 'deploy/.env', 'secrets/key.py', 'src/../oops.py']:
        assert not readable(forbidden)
    body = b'VALUE = 0\n'
    github = GitHub(config())
    github.request = lambda *args: (_ for _ in ()).throw(AssertionError('unchanged blob must be reused'))
    snapshot = {'commit': 'a'*40, 'entries': {
        path: {'sha': blob_sha(body.decode()), 'mode': '100644', 'type': 'blob', 'size': len(body)},
        'src/link.py': {'sha': 'c'*40, 'mode': '120000', 'type': 'blob', 'size': 4},
        'deploy/.env': {'sha': 'd'*40, 'mode': '100644', 'type': 'blob', 'size': 10},
    }}
    files, coverage = github.read_repository(snapshot, {path: body.decode()})
    assert files == {path: body.decode()} and 'src/link.py' in coverage['omitted_paths']
    assert coverage['reused_files'] == 1
    changed = 'VALUE = 1\n'
    snapshot['entries'][path]['sha'] = blob_sha(changed)
    github.request = lambda method, route: {'encoding': 'base64',
                                             'content': base64.b64encode(changed.encode()).decode()}
    files, coverage = github.read_repository(snapshot, {path: body.decode()})
    assert files == {path: changed} and coverage['fetched_files'] == 1


@pytest.mark.integration
async def test_current_and_historical_states_are_distinct_citable_and_owner_scoped(company):
    runner = make_maintainer(company)
    await runner.tick()
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
        original = digest(case['payload'])
        assess(conn, case_id=case['id'], disposition='invalidated', reason='Saved input did not contain claimed history',
               evidence={'turn': 'actual-original-input', 'commit': 'a'*40})
        value = report(conn, case, 'UHUMAN')
        assert 'problem' not in value and not value['finding_is_current_fact']
        assert value['historical_finding']['problem']
        assert digest(conn.execute('SELECT payload FROM maintenance_jobs WHERE id=%s', (case['id'],)).fetchone()['payload']) == original
        company.settings.company_code_commit = 'b'*40
        value = current_system(conn, company, ['UHUMAN'])
        assert value['repository']['commit'] == 'a'*40 and value['runtime']['code_commit'] == 'b'*40
        assert value['assessments'][0]['disposition'] == 'invalidated'
        assert current_system(conn, company, ['UOTHER'])['assessments'] == []
        read = repository_read(conn, {'path': SOURCE, 'commit': 'a'*40, 'line_count': 1})
        assert read['truncated'] and read['records'][0]['blob'] == 'd'*40
        assert repository_read(conn, {'query': 'bounded_sum'})['records']
        assert repository_read(conn, {'path': 'src/quant_company/missing.py'})['state'] == 'unknown'
        with pytest.raises(PolicyError):
            repository_read(conn, {'path': '../secret'})
        conn.execute("UPDATE repository_evidence SET checked_at=now()-interval '1 day'")
        assert current_system(conn, company, ['UHUMAN'])['repository']['state'] == 'stale'


@pytest.mark.integration
async def test_already_invalidated_finding_completes_without_a_stale_wait_reason(company):
    runner = make_maintainer(company)
    await runner.tick()
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='triage'").fetchone()
        assess(conn, case_id=case['id'], disposition='invalidated', reason='Original hypothesis disproved',
               evidence={'request_digest': digest(job['payload'])})
        conn.execute("UPDATE maintenance_jobs SET state='triage',error='daily_model_budget' WHERE id=%s",
                     (job['id'],))
    runner.store.finish_triage(job, Triage(finding=case['payload']['finding'], reason='No new failure'))
    with company.db.transaction() as conn:
        current = conn.execute('SELECT * FROM maintenance_jobs WHERE id=%s', (job['id'],)).fetchone()
        assert current['state'] == 'done' and current['error'] is None
        assert current['receipt']['reason'] == 'No new failure after case assessment.'
        assert conn.execute('SELECT payload FROM maintenance_jobs WHERE id=%s',
                            (case['id'],)).fetchone()['payload'] == case['payload']


@pytest.mark.integration
@pytest.mark.parametrize('drift', ['code', 'configuration'])
async def test_drift_archives_original_requests_and_rechecks_before_patch(company, drift):
    runner = make_maintainer(company)
    await runner.tick()
    with company.db.transaction() as conn:
        before = conn.execute('SELECT id,request FROM maintenance_calls ORDER BY id').fetchall()
        case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
    if drift == 'code':
        old = runner.github.snapshot()
        runner.github.snapshot = lambda: {**old, 'commit': 'f'*40}
    else:
        company.settings.company_max_depth = 2
    await runner.tick()
    assert runner.github.published == 0 and len(runner.provider.requests) == 1
    with company.db.transaction() as conn:
        assert conn.execute('SELECT id,request FROM maintenance_calls ORDER BY id').fetchall() == before
        assert conn.execute('SELECT state FROM maintenance_jobs WHERE id=%s', (case['id'],)).fetchone()['state'] == 'superseded'
        assert conn.execute('SELECT payload FROM maintenance_revisions WHERE job_id=%s', (case['id'],)).fetchone()['payload'] == case['payload']
        assert conn.execute("SELECT 1 FROM maintenance_jobs WHERE payload->>'predecessor'=%s", (str(case['id']),)).fetchone()


@pytest.mark.integration
async def test_reserved_triage_input_is_not_reused_after_rebind(company):
    runner = make_maintainer(company)
    runner.store.collect()
    job = runner.store.next_job()
    original = runner.store.prepare_call(job, 'triage', 'immutable old request')
    await runner.tick()
    with company.db.transaction() as conn:
        saved = conn.execute('SELECT request FROM maintenance_calls WHERE id=%s', (original['id'],)).fetchone()
        assert saved['request'] == original['request']
        assert conn.execute('SELECT count(*) AS n FROM maintenance_revisions').fetchone()['n'] == 1
        assert len(runner.provider.requests) == 1 and '-r1-triage' in runner.provider.requests[0].request_id


@pytest.mark.integration
async def test_finding_without_current_evidence_is_rejected(company):
    runner = make_maintainer(company)
    await runner.tick()
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
    finding = dict(case['payload']['finding'])
    finding['evidence_keys'] = [k for k in finding['evidence_keys'] if not k.startswith(('code:', 'system:'))]
    with pytest.raises(ValueError, match='current_implementation_evidence'):
        runner.store.finish_triage(case, Triage(finding=finding, reason='Missing implementation citation'))


@pytest.mark.integration
async def test_live_verification_never_transfers_silently_to_another_version(company):
    runner = make_maintainer(company)
    runner.refresh_repository()
    with company.db.transaction() as conn:
        runtime = current_system(conn, company, ['UHUMAN'])['runtime']
        conn.execute("""INSERT INTO system_verifications(id,feature,code_commit,config_digest,scope,evidence)
            VALUES ('fixture','slack_approval',%s,%s,'Document merge only',%s)""",
                     (runtime['code_commit'], runtime['config_digest'], Jsonb({'application': 'fixture'})))
        assert current_system(conn, company, ['UHUMAN'])['verifications'][0]['matches_running_version']
        company.settings.company_code_commit = 'f'*40
        assert not current_system(conn, company, ['UHUMAN'])['verifications'][0]['matches_running_version']
