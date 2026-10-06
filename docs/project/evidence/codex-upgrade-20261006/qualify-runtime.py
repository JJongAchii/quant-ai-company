"""Small actual subscription checks; durable stable IDs, no company effects or messages."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from quant_company.contracts import ProviderFault, ProviderRequest, ProviderSession
from quant_company.providers.codex_runner import (
    SUPPORTED_CLI_VERSION,
    CodexRunner,
    ProcessRunner,
    RunnerConfig,
    atomic_json,
    quant_output_model,
    request_digest,
    strict_json,
)

PREFIX = 'codex-upgrade1601'
MARKER = 'compatibility-1601-confirmed'


class Capture(ProcessRunner):
    def __init__(self):
        self.trace = []

    async def run(self, argv, **kwargs):
        result = await super().run(argv, **kwargs)
        if '--json' in argv and kwargs.get('stdin'):
            events = [strict_json(line, cli_web_event=True) for line in result.stdout.splitlines() if line.strip()]
            self.trace.append({'events': [e.get('type') for e in events],
                               'completed_usage': [e.get('usage') for e in events if e.get('type') == 'turn.completed'],
                               'items': [e['item'].get('type') for e in events if 'item' in e]})
        return result


def seed():
    return ProviderRequest(request_id=PREFIX + '-legacy-seed', model='gpt-5.6-luna', reasoning_effort='high',
        session=ProviderSession(id=PREFIX + '-legacy'),
        prompt='Remember this compatibility marker for the next turn: ' + MARKER + '. '
               'Return a complete AgentDecision with say exactly that marker. All action lists empty; follow_up null.')


async def main():
    case = sys.argv[1]
    profile, revision = sys.argv[2], int(sys.argv[3])
    jobs = Path('/state/jobs')
    receipt = jobs / (PREFIX + '-qualification-' + case + '.json')
    config = RunnerConfig(codex_home=Path('/state/auth'), backup_codex_home=Path('/state/backup-auth'),
                          jobs_dir=jobs, timeout_seconds=180)
    capture = Capture()
    runner = CodexRunner(config, process=capture)
    if case == 'legacy_seed':
        request, expected = seed(), MARKER
    elif case == 'legacy_resume':
        # Read the completed old-version seed by its unchanged ID; no new inference.
        prior = await runner.run(seed(), profile=profile, revision=revision)
        old = strict_json((jobs / (seed().request_id + '.json')).read_bytes())
        assert old['cli_version'] == '0.154.0'
        request = ProviderRequest(request_id=PREFIX + '-legacy-resume', model='gpt-5.6-luna', reasoning_effort='high',
            session=ProviderSession(id=PREFIX + '-legacy', previous_request_id=seed().request_id),
            prompt='Return the compatibility marker from the previous turn as say. Complete; all action lists empty; follow_up null.')
        expected = MARKER
    elif case == 'structured':
        request = ProviderRequest(request_id=PREFIX + '-structured', model='gpt-6.1-sol', reasoning_effort='high',
            prompt='Return a complete AgentDecision with say exactly "새 버전 연결 확인 완료". All action lists empty; follow_up null.')
        expected = '새 버전 연결 확인 완료'
    elif case == 'native_quant':
        contract = 'quant_brief_v4'
        model = quant_output_model(contract)
        value = model(disposition='hold', reason='Synthetic runtime qualification only; no research conclusion.',
                      kind='hypothesis', maturity='hypothesis', topic='research_validity').model_dump_json()
        request = ProviderRequest(request_id='quant-feed-' + PREFIX + '-native', model='gpt-6.1-sol',
                                  reasoning_effort='high', output_contract=contract,
                                  prompt='Return this synthetic compatibility object exactly; no research evaluation:\n' + value)
        expected = None
    elif case == 'web_quant':
        request = ProviderRequest(request_id='quant-feed-' + PREFIX + '-web', model='gpt-6.1-sol',
            reasoning_effort='high', output_contract='quant_search_v1', web_search=True,
            prompt='Use the live web search tool once to verify the Codex CLI changelog at '
                   'https://learn.chatgpt.com/docs/changelog . Return one results entry with that URL, '
                   'the page title and a short paraphrase of the CLI 0.160.1 release date. Do not execute other tools.')
        expected = None
    else:
        raise ValueError('unknown_qualification_case')
    material = {'case': case, 'cli_version': SUPPORTED_CLI_VERSION, 'request_id': request.request_id,
                'input_digest': request_digest(request), 'account': {'profile': profile, 'revision': revision}}
    if receipt.exists():
        record = strict_json(receipt.read_bytes())
        assert all(record[k] == v for k, v in material.items())
        if record['state'] == 'passed':
            print(json.dumps(record, ensure_ascii=False))
            return
        raise RuntimeError('Qualification needs reconciliation; never blindly replay a started call.')
    record = {**material, 'state': 'started', 'started_at': datetime.now(UTC).isoformat(),
              'real_chatgpt_subscription': True, 'company_effects': False, 'slack_messages': 0}
    atomic_json(receipt, record)
    try:
        response = await runner.run(request, profile=profile, revision=revision)
        assert response.decision.status == 'complete'
        assert not any((response.decision.tools, response.decision.delegations,
                        response.decision.messages, response.decision.memories, response.decision.follow_up))
        assert {'input_tokens', 'output_tokens'} <= response.usage.keys()
        assert response.usage['input_tokens'] > 0 and response.usage['output_tokens'] > 0
        if expected is not None:
            assert response.decision.say == expected and not response.decision.artifacts
        if case == 'legacy_resume':
            assert response.thread_id == prior.thread_id
            final = strict_json((jobs / (request.request_id + '.json')).read_bytes())
            # The new CLI must still report cumulative usage; service records per-turn deltas.
            for key, total in final['session_usage'].items():
                assert response.usage[key] == total - old['session_usage'].get(key, 0)
        if case in {'native_quant', 'web_quant'}:
            model = quant_output_model(request.output_contract)
            value = model.model_validate_json(response.decision.artifacts[0].content)
            if case == 'native_quant':
                assert value.disposition == 'hold'
            else:
                assert response.web_searches and len(value.results) == 1
                assert value.results[0].url == 'https://learn.chatgpt.com/docs/changelog'
        assert await runner.run(request, profile=profile, revision=revision) == response
        record.update(state='passed', completed_at=datetime.now(UTC).isoformat(),
                      result=response.model_dump(mode='json'), observed_cli_events=capture.trace,
                      cached_replay_equal=True, session_continuity_verified=case == 'legacy_resume')
    except BaseException as exc:
        record.update(state='reconciliation_required', error=type(exc).__name__,
                      fault_code=exc.code if isinstance(exc, ProviderFault) else None)
        atomic_json(receipt, record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        raise
    atomic_json(receipt, record)
    print(json.dumps(record, ensure_ascii=False), flush=True)


asyncio.run(main())
