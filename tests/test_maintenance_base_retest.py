"""Real PostgreSQL and pytest; GitHub/model/Slack are simulated, with no production effects."""
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.maintenance.github import blob_sha
from quant_company.maintenance.policy import digest
from quant_company.maintenance.runner import Maintainer

from .test_maintenance import ORIGINAL, SOURCE, GitHubFixture, config, prepare
from .test_maintenance_engineering import LocalCI


class NoModel:
    async def run(self, request):
        raise AssertionError('A verified unchanged patch must not spend another model call')


class RetestGitHub(GitHubFixture):
    def __init__(self, root, *, guard_error=None, changed_source=False):
        super().__init__()
        self.local = LocalCI(root)
        self.guard_error, self.changed_source = guard_error, changed_source

    def snapshot(self):
        return {'commit': 'b' * 40, 'tree': 'f' * 40, 'paths': [SOURCE],
                'entries': {SOURCE: {'sha': blob_sha(ORIGINAL + ('\n# changed' if self.changed_source else '')),
                                    'type': 'blob', 'mode': '100644'}}}

    def verify_base_retest(self, job):
        assert job['receipt']['ci']['state'] == 'passed'
        if self.guard_error:
            raise ValueError(self.guard_error)

    def publish(self, job, payload):
        assert payload['snapshot']['commit'] == self.snapshot()['commit']
        result = super().publish(job, payload)
        return {**result, 'base': payload['snapshot']['commit']}

    def ci(self, receipt):
        files = self.child['payload']['changes']
        test = next(path for path in files if path.startswith('tests/'))
        before = self.local.run_pytest({SOURCE: ORIGINAL, test: files[test]}, 'retest-base')
        after = self.local.run_pytest(files, 'retest-candidate')
        assert before.returncode == 1 and '1 failed' in before.stdout
        assert after.returncode == 0 and '1 passed' in after.stdout
        return {'state': 'passed', 'url': 'https://example.invalid/simulated-ci'}


def seeded(company, tmp_path, **options):
    request = prepare(company)
    runner = Maintainer(company, config(), github=RetestGitHub(tmp_path, **options), provider=NoModel())
    runner.store.initialize()
    identity = uuid4()
    test = 'tests/test_maintenance_regression_' + identity.hex + '.py'
    finding = {'problem_key': 'bounded_sum_subtracts', 'title': 'Correct arithmetic helper',
               'problem': 'The helper returns subtraction instead of addition.',
               'reproduction': 'Calling bounded_sum(3, 2) returns 1 instead of 5.',
               'expected': 'The public consumer must return 5 for inputs 3 and 2.',
               'category': 'platform_defect', 'hypothesis': 'The arithmetic operator is incorrect.',
               'paths': [SOURCE], 'evidence_keys': ['message:recorded'],
               'evaluation': {'mode': 'regression', 'success_criterion': 'The same public consumer assertion fails on base and passes on the candidate.'}}
    changes = {SOURCE: ORIGINAL.replace('left - right', 'left + right'),
               test: 'def test_public_consumer():\n    from quant_company.tools import bounded_sum\n    assert bounded_sum(3, 2) == 5\n'}
    review = {'evidence': [], 'roster': []}
    with company.db.transaction() as conn:
        # Exercise this retained request without collecting unrelated fixture observations.
        conn.execute("UPDATE maintenance_control SET next_observe_at=now()+interval '1 hour' WHERE id=1")
        project = company._project(conn, request['project_id'])
        task = conn.execute('SELECT * FROM tasks WHERE project_id=%s', (project['id'],)).fetchone()
        payload = {'owners': ['UHUMAN'], 'request_project_id': str(project['id']),
                   'request_task_id': str(task['id']), 'request_revision': project['revision'],
                   'instruction': 'Repair the public arithmetic consumer', 'observations': [],
                   'finding': finding, 'evaluation_plan_digest': digest(finding['evaluation']),
                   'replay_inputs': {}, 'replay_inputs_digest': digest({}),
                   'review': review, 'review_digest': digest(review), 'originals': {SOURCE: ORIGINAL},
                   'changes': changes, 'patch_digest': digest(changes),
                   'patch_attempt': runner.config.max_patch_attempts, 'candidate_attempts': [{}, {}],
                   'snapshot': {'commit': 'a' * 40, 'tree': 'e' * 40, 'paths': [SOURCE],
                                'entries': {SOURCE: {'sha': blob_sha(ORIGINAL), 'type': 'blob', 'mode': '100644'}}},
                   'diagnostic_revision': 0}
        receipt = {'head': 'c' * 40, 'base': 'a' * 40, 'branch': 'maintenance/' + str(identity),
                   'ci': {'state': 'passed', 'url': 'https://example.invalid/original-ci'}}
        conn.execute("INSERT INTO maintenance_jobs(id,kind,state,error,payload,receipt) VALUES (%s,'repair','blocked','pr_ref_moved',%s,%s)",
                     (identity, Jsonb(payload), Jsonb(receipt)))
        conn.execute('INSERT INTO maintenance_calls(id,job_id,request,response) VALUES (%s,%s,%s,%s)',
                     ('original-' + str(identity), identity, Jsonb({'original': True}), Jsonb({'recorded': True})))
        parent = conn.execute('SELECT * FROM maintenance_jobs WHERE id=%s', (identity,)).fetchone()
        calls = conn.execute('SELECT to_jsonb(c) AS body FROM maintenance_calls c').fetchall()
    return runner, parent, calls


@pytest.mark.integration
async def test_blocked_base_move_retests_frozen_patch_to_pr_without_model_or_reset(company, tmp_path):
    runner, parent, calls = seeded(company, tmp_path)
    result = await runner.tick()
    assert result['state'] == 'advanced', 'A known base move must retain the candidate and create a retest child'
    with company.db.transaction() as conn:
        old = conn.execute('SELECT * FROM maintenance_jobs WHERE id=%s', (parent['id'],)).fetchone()
        child = conn.execute('SELECT * FROM maintenance_jobs WHERE id=%s', (old['receipt'].get('recheck_id'),)).fetchone()
        assert old['state'] == parent['state'] and old['error'] == parent['error']
        assert old['payload'] == parent['payload']
        assert {key: value for key, value in old['receipt'].items() if key != 'recheck_id'} == parent['receipt']
        assert conn.execute('SELECT to_jsonb(c) AS body FROM maintenance_calls c').fetchall() == calls
    assert child and child['state'] == 'publish'
    assert runner.store.retest_base(parent, child['payload']['snapshot'], child['payload']['diagnosis']) == str(child['id'])
    assert child['payload']['patch_attempt'] == parent['payload']['patch_attempt']
    assert child['payload']['candidate_attempts'] == parent['payload']['candidate_attempts']
    assert child['payload']['finding'] == parent['payload']['finding']
    assert child['payload']['evaluation_plan_digest'] == parent['payload']['evaluation_plan_digest']
    assert child['payload']['changes'][SOURCE] == parent['payload']['changes'][SOURCE]
    old_test = next(path for path in parent['payload']['changes'] if path.startswith('tests/'))
    new_test = next(path for path in child['payload']['changes'] if path.startswith('tests/'))
    assert new_test != old_test and child['payload']['changes'][new_test] == parent['payload']['changes'][old_test]
    assert runner.store.next_job()['id'] == child['id']
    runner.github.child = child
    for _ in range(3):
        assert (await runner.tick())['state'] == 'advanced'
    with company.db.transaction() as conn:
        ready = conn.execute('SELECT * FROM maintenance_jobs WHERE id=%s', (child['id'],)).fetchone()
        assert ready['state'] == 'pr_open' and ready['receipt']['base'] == 'b' * 40
        assert conn.execute('SELECT to_jsonb(c) AS body FROM maintenance_calls c').fetchall() == calls
    assert runner.github.published == runner.github.prs == 1


@pytest.mark.parametrize('fault,reason', [
    ('changed_source', 'base_retest_original_source_changed'),
    ('changed_head', 'base_retest_candidate_ref_changed'),
    ('failed_ci', 'base_retest_candidate_ci_not_passed'),
    ('owner_revoked', 'review_owner_authorization_changed'),
    ('revision', 'review_revision_changed'),
    ('churn', 'evidence_churn_requires_review'),
])
async def test_retest_guards_never_publish_or_spend_model_calls(company, tmp_path, fault, reason):
    options = {'changed_source': fault == 'changed_source'}
    if fault in {'changed_head', 'failed_ci'}:
        options['guard_error'] = reason
    runner, parent, calls = seeded(company, tmp_path, **options)
    if fault == 'owner_revoked':
        company.settings.slack_allowed_users = []
    with company.db.transaction() as conn:
        if fault == 'revision':
            conn.execute('UPDATE projects SET revision=revision+1 WHERE id=%s', (parent['payload']['request_project_id'],))
        if fault == 'churn':
            conn.execute("UPDATE maintenance_jobs SET payload=jsonb_set(payload,'{diagnostic_revision}','3') WHERE id=%s", (parent['id'],))
    result = await runner.tick()
    assert result == {'state': 'blocked', 'reason': reason}
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM maintenance_jobs WHERE kind='repair'").fetchone()['n'] == 1
        blocked = conn.execute('SELECT * FROM maintenance_jobs WHERE id=%s', (parent['id'],)).fetchone()
        assert blocked['state'] == 'blocked' and blocked['error'] == reason
        assert not blocked['receipt'].get('recheck_id')
        assert conn.execute('SELECT to_jsonb(c) AS body FROM maintenance_calls c').fetchall() == calls
    assert runner.github.published == runner.github.prs == 0
