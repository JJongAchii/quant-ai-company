import json
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from psycopg.types.json import Jsonb

from quant_company.maintenance.applications import Applications, accept_approval, approval_command
from quant_company.maintenance.policy import digest
from quant_company.maintenance.store import Store
from quant_company.slack import SlackIngress
from tests.test_maintenance import config, prepare


def pending(company, *, project=None, number=3):
    project = project or prepare(company)['project_id']
    Store(company, config()).initialize()
    identity = str(uuid4())
    changes = {'docs/maintenance.md': 'updated documentation\n'}
    payload = {'observations': [{'project_id': str(project)}], 'changes': changes, 'patch_digest': digest(changes)}
    receipt = {'head': 'c'*40, 'base': 'a'*40, 'branch': 'maintenance/'+identity,
               'pr': {'number': number, 'url': f'https://github.com/JJongAchii/quant-ai-company/pull/{number}'}}
    with company.db.transaction() as conn:
        conn.execute("INSERT INTO maintenance_jobs(id,kind,state,payload,receipt) VALUES (%s,'repair','pr_open',%s,%s)",
                     (identity, Jsonb(payload), Jsonb(receipt)))
        message_id = str(uuid5(NAMESPACE_URL, f'maintenance-pr:{identity}:{project}'))
        company._message(conn, company._project(conn, project), None, 'director', 'maintenance',
                         'Review PR', message_id=message_id)
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='1.2' WHERE id=%s", (message_id,))
    return identity, project


def approve(company, text='반영해', key='slack:TTEST:CQUANT:2.2:director'):
    return accept_approval(company, text=text, owner='UHUMAN', channel='CQUANT', thread_ts='1.1', event_key=key)


@pytest.mark.parametrize('text', ['반영하지 마', '반영해도 될까?', '"반영해"라고 말하면?', '승인 없이 반영해', '설명해'])
def test_conversation_is_not_approval(text):
    assert approval_command(text) == (False, None)


@pytest.mark.parametrize('text,number', [('반영해', None), ('반영해 주세요.', None), ('네, 반영해', None),
                                      ('PR #3 반영해', 3), ('3번 적용해줘', 3)])
def test_short_approval_commands(text, number):
    assert approval_command(text) == (True, number)


@pytest.mark.integration
def test_slack_approval_is_exact_and_duplicate_has_no_model_or_second_notice(company, credentials):
    identity, _ = pending(company)
    ingress = SlackIngress(company.settings, company, credentials)
    payload = {'team_id': 'TTEST', 'api_app_id': 'A0', 'event': {
        'type': 'message', 'user': 'UHUMAN', 'channel': 'CQUANT', 'thread_ts': '1.1', 'ts': '2.2', 'text': '반영해'}}
    first = ingress.accept('director', payload, credentials['director'])
    assert first['maintenance_approval'] == 'approved'
    assert ingress.accept('director', payload, credentials['director'])['duplicate']
    with company.db.transaction() as conn:
        rows = conn.execute('SELECT * FROM maintenance_applications').fetchall()
        assert len(rows) == 1 and str(rows[0]['job_id']) == identity and rows[0]['head'] == 'c'*40
        assert rows[0]['approval_text'] == '반영해'
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) AS n FROM messages WHERE text LIKE '%%승인을 기록%%'").fetchone()['n'] == 1
    payload['event']['user'] = 'UOUTSIDER'
    assert ingress.accept('director', payload, credentials['director'])['ignored']


@pytest.mark.integration
def test_two_prs_require_target_and_other_thread_cannot_approve(company):
    _, project = pending(company)
    second, _ = pending(company, project=project, number=4)
    assert approve(company)['maintenance_approval'] == 'ambiguous'
    assert approve(company, 'PR #4 반영해', key='explicit')['maintenance_approval'] == 'approved'
    assert accept_approval(company, text='반영해', owner='UHUMAN', channel='CQUANT', thread_ts='unknown', event_key='x') is None
    with company.db.transaction() as conn:
        row = conn.execute('SELECT job_id FROM maintenance_applications').fetchone()
        assert str(row['job_id']) == second


@pytest.mark.integration
def test_delayed_slack_approval_cannot_select_pr_announced_after_it(company):
    first, project = pending(company)
    second, _ = pending(company, project=project, number=4)
    with company.db.transaction() as conn:
        notice = str(uuid5(NAMESPACE_URL, f'maintenance-pr:{second}:{project}'))
        conn.execute("UPDATE outbox SET sent_ts='3.3' WHERE id=%s", (notice,))
    result = accept_approval(company, text='반영해', owner='UHUMAN', channel='CQUANT', thread_ts='1.1',
                             event_key='slack:TTEST:CQUANT:2.2:director', event_ts='2.2')
    assert result['maintenance_approval'] == 'approved'
    with company.db.transaction() as conn:
        assert str(conn.execute('SELECT job_id FROM maintenance_applications').fetchone()['job_id']) == first


class Merger:
    def __init__(self, deployment=False):
        self.merges, self.deployment = 0, deployment

    def validate_application(self, job):
        return {'validated_base': 'a'*40, 'requires_deployment': self.deployment}

    def merge_application(self, job, receipt):
        self.merges += 1
        return {'merge_commit': 'd'*40}

    def reconcile_application(self, job, receipt):
        return {'merge_commit': 'd'*40}


@pytest.mark.integration
@pytest.mark.parametrize('deployment,state', [(False, 'complete'), (True, 'deploy_pending')])
def test_approved_merge_routes_docs_or_runtime_and_replay_is_noop(company, deployment, state):
    pending(company)
    approve(company)
    github = Merger(deployment)
    runner = Applications(company, config(), github)
    assert runner.advance()['state'] == state
    assert runner.advance() is None and github.merges == 1


@pytest.mark.integration
def test_crash_after_merge_only_reconciles_and_changed_head_blocks(company):
    identity, _ = pending(company)
    approve(company)
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_applications SET state='merging',receipt=%s",
                     (Jsonb({'validated_base': 'a'*40, 'requires_deployment': False, 'pr_number': 3}),))
    github = Merger()
    assert Applications(company, config(), github).advance()['state'] == 'complete'
    assert github.merges == 0
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_applications SET state='approved'")
        conn.execute("UPDATE maintenance_jobs SET receipt=jsonb_set(receipt,'{head}',%s) WHERE id=%s",
                     (Jsonb('e'*40), identity))
    assert Applications(company, config(), github).advance()['reason'] == 'approved_head_changed'
    assert github.merges == 0


@pytest.mark.integration
def test_revoked_owner_cannot_apply_already_approved_candidate(company):
    pending(company)
    approve(company)
    company.settings.slack_allowed_users = []
    github = Merger()
    result = Applications(company, config(), github).advance()
    assert result['state'] == 'blocked' and github.merges == 0


@pytest.mark.parametrize('draft', [False, True])
def test_github_does_not_merge_changed_content_and_reconciles_lost_response(draft):
    import httpx

    from quant_company.maintenance.github import GitHub, blob_sha

    candidate = {'id': str(uuid4()), 'payload': {'changes': {'docs/example.md': 'new'}, 'originals': {'docs/example.md': 'old'}},
                 'receipt': {'head': 'c'*40, 'base': 'a'*40, 'branch': 'maintenance/test', 'pr': {'number': 3}}}
    merged, writes = False, []
    changed = False

    def handler(request):
        nonlocal merged, draft
        path = request.url.path
        if path == '/graphql':
            draft = False
            return httpx.Response(200, json={'data': {'markPullRequestReadyForReview': {'pullRequest': {'isDraft': False}}}})
        if path.endswith('/files'):
            return httpx.Response(200, json=[{'filename': 'docs/example.md', 'status': 'modified',
                                             'sha': blob_sha('tampered' if changed else 'new')}])
        if path.endswith('/pulls/3'):
            return httpx.Response(200, json={'head': {'sha': 'c'*40, 'ref': 'maintenance/test',
                    'repo': {'full_name': 'JJongAchii/quant-ai-company'}}, 'base': {'ref': 'main', 'sha': 'a'*40},
                    'state': 'closed' if merged else 'open', 'draft': draft, 'node_id': 'PR_fixture', 'changed_files': 1,
                    'mergeable': True, 'merged': merged, 'merge_commit_sha': 'd'*40, 'html_url': 'https://github.com/test'})
        if path.endswith('/git/ref/heads/main'):
            # Actual GitHub acceptance: PR.base.sha still names the original a... snapshot.
            return httpx.Response(200, json={'object': {'sha': 'e'*40}})
        if path.endswith('/contents/docs/example.md'):
            assert request.url.params['ref'] == 'e'*40
            return httpx.Response(200, json={'sha': blob_sha('old')})
        if path.endswith('/merge'):
            assert json.loads(request.content)['sha'] == 'c'*40
            writes.append(path)
            merged = True
            raise httpx.ReadTimeout('Lost successful merge response', request=request)
        if path.endswith('/git/commits/'+'d'*40):
            return httpx.Response(200, json={'sha': 'd'*40, 'parents': [{'sha': 'e'*40}, {'sha': 'c'*40}]})
        raise AssertionError(path)

    github = GitHub(config(), transport=httpx.MockTransport(handler))
    github.token = lambda: 'fixture'
    github.ci = lambda r: {'state': 'passed'}
    checked = github.validate_application(candidate)
    changed = True
    with pytest.raises(ValueError, match='content_changed'):
        github.validate_application(candidate)
    changed = False
    assert github.merge_application(candidate, checked)['merge_commit'] == 'd'*40
    assert len(writes) == 1
