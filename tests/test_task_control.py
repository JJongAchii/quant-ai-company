import json

import pytest

from quant_company.company import Company, PolicyError
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.slack import SlackIngress, SlackOutbox
from quant_company.task_control import immediate

from .test_company import completed
from .test_slack import event


def submit(company, key, text, project=None, **kwargs):
    return company.ingest(event_key=key, text=text, owner='UHUMAN', project_id=project,
                          channel='CQUANT', thread_ts='100.001', interpret=True, **kwargs)


def turn_for(company, task_id):
    with company.db.transaction() as conn:
        return str(conn.execute('SELECT id FROM turns WHERE task_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',
                                (task_id,)).fetchone()['id'])


def route(company, request, action, **arguments):
    turn = turn_for(company, request['task_id'])
    prepared = company.prepare_turn(turn)
    assert prepared['state'] == 'ready'
    context = json.loads(prepared['request']['prompt'].split('TASK DATA JSON:\n')[1])
    assert context['task']['kind'] == 'routing'
    response = ProviderResponse(request_id=turn, decision=AgentDecision(
        say='', status='continue', tools=[{'name': 'task_control', 'arguments': {'action': action, **arguments}}]))
    return company.commit_turn(turn, response)


@pytest.mark.parametrize(('text', 'action'), [
    ('중단해줘', 'pause'), ('이어서 진행해.', 'resume'), ('어디까지 진행됐어?', 'status'),
    ('중단하지 마', None), ('"중단해"라고 하면 어떻게 돼?', None), ('다른 스레드 중단해', None),
    ('중단해도 괜찮을까?', None), ('월간으로 바꿔줘', None),
])
def test_only_unambiguous_standalone_controls_are_immediate(text, action):
    assert immediate(text) == action


def test_amend_fences_running_result_before_classification_and_supersedes_it(company, credentials):
    first = submit(company, 'first', '국내 ETF를 일간 기준으로 정리해줘')
    old = turn_for(company, first['task_id'])
    company.prepare_turn(old)
    amendment = submit(company, 'amend', '월간으로 바꿔줘', first['project_id'])
    assert company.commit_turn(old, completed(old))['state'] == 'defer'
    assert company.prepare_turn(old)['state'] == 'defer'
    assert SlackOutbox(company, credentials).claim() is None
    result = route(company, amendment, 'amend')
    assert result['intent']['revision'] == 2
    assert company.commit_turn(old, completed(old))['state'] == 'stale'
    state = company.project_state(first['project_id'])
    assert '국내 ETF' in state['project']['instruction'] and '월간으로 바꿔줘' in state['project']['instruction']
    assert not any(m['text'] == 'Verified fixture completion' for m in state['messages'])
    assert len([t for t in state['tasks'] if t['kind'] == 'work' and t['status'] != 'superseded']) == 1


def test_question_releases_existing_response_and_outbox_without_replacing_mandate(company, credentials):
    first = submit(company, 'first', '국내 ETF 보고서')
    old = turn_for(company, first['task_id'])
    original = company.prepare_turn(old)['request']
    response = completed(old, 'Existing answer')
    company.commit_turn(old, response)
    follow = submit(company, 'question', '쉽게 설명해줘', first['project_id'])
    assert SlackOutbox(company, credentials).claim() is None
    assert route(company, follow, 'followup')['intent']['action'] == 'followup'
    state = company.project_state(first['project_id'])
    assert state['project']['revision'] == 1
    assert state['project']['instruction'] == '국내 ETF 보고서'
    claimed = SlackOutbox(company, credentials).claim()
    assert claimed is not None
    with company.db.transaction() as conn:
        assert conn.execute('SELECT status FROM outbox WHERE id=%s', (claimed['id'],)).fetchone()['status'] == 'sending'
    assert original['request_id'] == old


def test_running_response_held_for_question_is_reused_after_restart(company):
    first = submit(company, 'first', 'Work')
    old = turn_for(company, first['task_id'])
    reserved = company.prepare_turn(old)['request']
    follow = submit(company, 'question', '그 뜻을 설명해줘', first['project_id'])
    assert company.commit_turn(old, completed(old))['state'] == 'defer'
    route(company, follow, 'followup')
    restarted = Company(company.settings, company.roles)
    assert restarted.prepare_turn(old)['request'] == reserved
    assert restarted.commit_turn(old, completed(old))['state'] == 'completed'


def test_pause_resume_and_status_are_deterministic_even_without_model_budget(company, credentials):
    ingress = SlackIngress(company.settings, company, credentials)
    first = ingress.accept('director', event(credentials), credentials['director'])
    old = turn_for(company, first['task_id'])
    company.prepare_turn(old)
    with company.db.transaction() as conn:
        conn.execute("UPDATE runtime_control SET paused_until=now()+interval '1 hour',reason='quota'")
    for ts, text in [('101.1', '중단해'), ('102.1', '상태'), ('103.1', '이어서 진행해')]:
        payload = event(credentials, text=text, ts=ts, thread_ts='100.001', type='message')
        result = ingress.accept('director', payload, credentials['director'])
        assert not result['duplicate']
        assert ingress.accept('director', payload, credentials['director'])['duplicate']
    state = company.project_state(first['project_id'])
    assert state['project']['status'] == 'active' and state['project']['revision'] == 3
    assert len(state['turns']) == 2  # Original work and resumed work; no model-based controls.
    assert any('quota' in m['text'] for m in state['messages'] if m['kind'] == 'status')
    assert company.commit_turn(old, completed(old))['state'] == 'stale'
    assert all(t['kind'] == 'control' for t in state['tasks'] if t['turn_count'] == 0)


def test_fifo_router_survives_revision_and_cannot_reserve_out_of_order(company):
    first = submit(company, 'first', '국내 ETF 비교')
    one = submit(company, 'one', '월간으로 바꿔줘', first['project_id'])
    two = submit(company, 'two', '채권 ETF부터 우선 정리해줘', first['project_id'])
    later = turn_for(company, two['task_id'])
    assert company.prepare_turn(later)['state'] == 'defer'
    route(company, one, 'amend')
    assert route(company, two, 'amend')['intent']['revision'] == 3
    state = company.project_state(first['project_id'])
    assert all(text in state['project']['instruction'] for text in ['국내 ETF', '월간', '채권 ETF'])
    assert len([t for t in state['tasks'] if t['status'] == 'pending']) == 1


def test_ambiguity_holds_work_and_reply_resolves_with_saved_question(company):
    first = submit(company, 'first', '국내 ETF 비교')
    old = turn_for(company, first['task_id'])
    question = submit(company, 'unclear', '그건 빼줘', first['project_id'])
    route(company, question, 'clarify', question='어떤 항목을 제외할까요?')
    assert company.prepare_turn(old)['seconds'] == 1800  # No fast polling while waiting for the owner.
    reply = submit(company, 'clarify-reply', '주식 ETF를 제외해줘', first['project_id'])
    route(company, reply, 'amend')
    state = company.project_state(first['project_id'])
    assert state['project']['status'] == 'active' and state['project']['clarification'] is None
    assert '어떤 항목' in state['project']['instruction'] and '주식 ETF' in state['project']['instruction']


def test_owner_controls_cannot_cross_threads_or_be_invoked_by_employee(company):
    first = submit(company, 'first', 'Work')
    with pytest.raises(PolicyError):
        company.ingest(event_key='intruder', text='중단해', owner='ANOTHER', project_id=first['project_id'],
                       control_action='pause')
    old = turn_for(company, first['task_id'])
    company.roles['director'] = company.roles['director'].model_copy(update={'tools': ['task_control']})
    company.prepare_turn(old)
    with pytest.raises(PolicyError, match='only available'):
        company.commit_turn(old, ProviderResponse(request_id=old, decision=AgentDecision(
            say='', status='continue', tools=[{'name': 'task_control', 'arguments': {'action': 'pause'}}])))
    assert company.project_state(first['project_id'])['project']['status'] == 'active'


def test_direct_stop_supersedes_unfinished_intent_and_does_not_resume_it(company):
    first = submit(company, 'first', 'Work')
    amendment = submit(company, 'amend', '월간으로 바꿔줘', first['project_id'])
    router = turn_for(company, amendment['task_id'])
    company.prepare_turn(router)
    submit(company, 'stop', '중단해', first['project_id'], control_action='pause')
    assert company.commit_turn(router, completed(router))['state'] == 'stale'
    assert company.project_state(first['project_id'])['project']['status'] == 'paused'


def test_legacy_ingress_duplicate_survives_control_upgrade(company, credentials):
    company.ingest(event_key='slack:TTEST:CQUANT:100.001:director', text='연구를 준비해 주세요',
                   owner='UHUMAN', channel='CQUANT', thread_ts='100.001')
    assert SlackIngress(company.settings, company, credentials).accept('director', event(credentials), credentials['director'])['duplicate']


def test_clarification_in_one_thread_cannot_starve_colleagues_in_another(company):
    first = submit(company, 'first', '국내 ETF 비교')
    question = submit(company, 'unclear', '그건 빼줘', first['project_id'])
    route(company, question, 'clarify', question='어떤 항목을 제외할까요?')
    other = company.ingest(event_key='another-thread', text='Separate work', owner='UHUMAN')
    turn = turn_for(company, other['task_id'])
    company.prepare_turn(turn)
    company.commit_turn(turn, ProviderResponse(request_id=turn, decision=AgentDecision(
        say='', status='wait', delegations=[{'agent': 'data', 'instruction': 'Inspect data'}])))
    state = company.project_state(other['project_id'])
    child = next(t for t in state['tasks'] if t['agent'] == 'data')
    assert company.prepare_turn(turn_for(company, child['id']))['state'] == 'ready'
