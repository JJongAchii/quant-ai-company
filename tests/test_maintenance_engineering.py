"""Engineering loop with real PostgreSQL/pytest; model and GitHub transport are simulated."""

import json
import os
import subprocess
import sys

import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance.policy import CodeQuery, Patch, apply_patch, digest
from quant_company.maintenance.requests import submit
from quant_company.maintenance.runner import Maintainer

from .test_maintenance import SOURCE, GitHubFixture, config, prepare

NEW = 'src/quant_company/research_labels.py'
BASE = 'def bounded_sum(left, right):\n    return left + right\n\ndef format_label(value):\n    return value\n'
GOOD = 'from .research_labels import normalize_label\n\ndef bounded_sum(left, right):\n    return left + right\n\ndef format_label(value):\n    return normalize_label(value)\n'


class EngineeringModel:
    def __init__(self, *, weaken_test=False):
        self.requests = []
        self.weaken_test = weaken_test

    async def run(self, request):
        self.requests.append(request)
        data = json.loads(request.prompt.split('EVIDENCE JSON:\n')[1])
        if request.request_id.endswith('-triage'):
            result = {'reason': 'Inspect the caller before choosing an implementation.', 'inspect': [{'path': SOURCE}]}
        elif '-triage-i' in request.request_id:
            assert 'return value' in data['investigated_code'][0]['content']
            result = {'reason': 'The explicit feature request needs a new implementation and caller wiring.', 'finding': {
                'problem_key': 'implement_label_normalization', 'title': 'Implement label normalization',
                'problem': 'The owner requested label normalization through the existing formatter.',
                'reproduction': 'The existing format_label returns whitespace and original case unchanged.',
                'expected': 'Normalize whitespace and case through format_label; preserve existing arithmetic.',
                'category': 'feature_request', 'hypothesis': 'Implement a reusable normalizer and connect the caller.',
                'evaluation': {'mode': 'regression', 'success_criterion': 'The public formatter returns a stripped lowercase label and existing sum behavior remains correct.'},
                'evidence_keys': [data['observations'][0]['key'], data['investigated_code'][0]['key']],
                'paths': [SOURCE], 'new_paths': [NEW],
            }}
        elif request.request_id.endswith('-patch'):
            result = {'summary': 'Implement and connect the requested formatter with a consumer regression.', 'edits': [
                {'path': SOURCE, 'old': BASE, 'new': GOOD.replace('left + right', 'left - right')},
                {'path': NEW, 'old': '', 'new': 'def normalize_label(value):\n    return value.strip().casefold()\n'},
                {'path': data['required_new_test_path'], 'old': '', 'new':
                    'def test_formatter_consumer():\n    from quant_company.tools import format_label\n    assert format_label("  FED  ") == "fed"\n'},
            ]}
        else:
            assert 'assert 1 == 5' in data['validation_feedback']['log_excerpt']
            assert data['finding']['evaluation']['success_criterion'].endswith('sum behavior remains correct.')
            edits = [{'path': SOURCE, 'old': 'left - right', 'new': 'left + right'}]
            if self.weaken_test:
                edits.append({'path': data['required_new_test_path'], 'old': 'assert format_label("  FED  ") == "fed"', 'new': 'assert True'})
            result = {'summary': 'Repair the arithmetic regression identified in actual pytest output.', 'edits': edits}
        return ProviderResponse(request_id=request.request_id, provider='fixture', decision=AgentDecision(
            say='Simulated engineering proposal', status='complete', artifacts=[{'title': 'Proposal', 'content': json.dumps(result)}]))


class LocalCI(GitHubFixture):
    def __init__(self, root):
        super().__init__()
        self.root, self.candidates, self.results = root, [], []

    def read_repository(self, snapshot, previous=None):
        return {SOURCE: BASE}, {'read_files': 1, 'omitted_paths': []}

    def read_files(self, snapshot, paths):
        return {SOURCE: BASE}

    def publish(self, job, payload):
        self.published += 1
        self.candidates.append(dict(payload['changes']))
        attempt = payload.get('patch_attempt', 1)
        return {'branch': 'maintenance/' + str(job['id']) + (f'-a{attempt}' if attempt > 1 else ''),
                'head': str(attempt) * 40, 'base': 'a' * 40}

    def run_pytest(self, files, name):
        root = self.root / name
        root.mkdir()
        for path, text in {**files, 'src/quant_company/__init__.py': ''}.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return subprocess.run([sys.executable, '-m', 'pytest', '-q', '--rootdir', str(root), str(root / 'tests')],
                              cwd=root, capture_output=True, text=True, timeout=30,
                              env={'PATH': os.environ['PATH'], 'PYTHONPATH': str(root / 'src'),
                                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})

    def ci(self, receipt):
        changes = self.candidates[-1]
        test_path = next(p for p in changes if p.startswith('tests/'))
        base = self.run_pytest({SOURCE: BASE, test_path: changes[test_path]}, f'base-{self.published}')
        assert base.returncode == 1 and '1 failed' in base.stdout
        regression = self.run_pytest(changes, f'regression-{self.published}')
        assert regression.returncode == 0
        controls = {'tests/test_existing_control.py': 'def test_existing_arithmetic():\n    from quant_company.tools import bounded_sum\n    assert bounded_sum(3, 2) == 5\n'}
        suite = self.run_pytest({**changes, **controls}, f'suite-{self.published}')
        self.results.append(suite)
        return {'state': 'passed' if suite.returncode == 0 else 'failed', 'url': 'https://example.com/simulated-ci',
                'run_id': self.published, 'attempt': 1}

    def ci_failure(self, receipt):
        return {'head': receipt['head'], 'log_excerpt': self.results[-1].stdout,
                'steps': [{'name': 'Regression reproduces on base', 'conclusion': 'success'},
                          {'name': 'PostgreSQL, Temporal and service regression tests', 'conclusion': 'failure'}]}


@pytest.mark.parametrize('weaken_test', [False, True])
async def test_inspect_implement_real_test_failure_repair_and_pr(company, tmp_path, weaken_test):
    request = prepare(company)
    runner = Maintainer(company, config(), github=LocalCI(tmp_path), provider=EngineeringModel(weaken_test=weaken_test))
    runner.store.initialize()
    with company.db.transaction() as conn:
        project = company._project(conn, request['project_id'])
        task = conn.execute('SELECT * FROM tasks WHERE project_id=%s', (project['id'],)).fetchone()
        assert submit(conn, company, project, task)['accepted']
    for _ in range(12):
        await runner.tick()
        with company.db.transaction() as conn:
            case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
        if case and case['state'] in {'pr_open', 'blocked'}:
            break
    assert case['payload']['patch_attempt'] == 2
    assert len(case['payload']['candidate_attempts']) == 1
    assert case['payload']['evaluation_plan_digest'] == digest(case['payload']['finding']['evaluation'])
    assert len(case['payload']['investigation_requests']) == 1
    if weaken_test:
        assert case['state'] == 'blocked' and case['error'] == 'cannot_change_reproduced_regression'
        assert runner.github.published == 1 and runner.github.prs == 0
    else:
        assert case['state'] == 'pr_open', case['error']
        assert runner.github.published == 2 and runner.github.prs == 1
        assert case['receipt']['branch'].endswith('-a2')
        assert runner.github.results[-1].returncode == 0
        assert NEW in case['payload']['changes']
        assert len(runner.provider.requests) == 4


def test_declared_new_module_and_security_boundary():
    with pytest.raises(ValueError, match='protected_or_unknown_path'):
        apply_patch(Patch(summary='Undeclared new feature module', edits=[{'path': NEW, 'old': '', 'new': 'value = 1\n'}]), {}, 'abc')
    assert CodeQuery(path=SOURCE).line_count == 160


async def test_maintenance_original_is_durable_and_idempotent(company, monkeypatch):
    from quant_company.maintenance.investigation import external_research

    from .test_web_research import HTML, URL, original

    prepare(company)
    runner = Maintainer(company, config(), github=GitHubFixture(), provider=EngineeringModel())
    runner.store.initialize()
    runner.store.collect()
    job = runner.store.next_job()
    calls = []

    def fetch(url):
        calls.append(url)
        return original(url)

    monkeypatch.setattr('quant_company.web_fetch.fetch', fetch)
    first = await external_research(runner, job, None, [URL], 0)
    second = await external_research(runner, job, None, [URL], 1)
    assert first == second and len(calls) == 1
    assert first[0]['ok'] and first[0]['published_at'] == '2026-09-17'
    with company.db.transaction() as conn:
        saved = conn.execute('SELECT * FROM web_requests').fetchone()
        assert saved['original'] == HTML and saved['maintenance_job_id'] == job['id']
        assert saved['turn_id'] is None
