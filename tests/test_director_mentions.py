"""Real PostgreSQL and mocked Slack delivery; no external notifications."""

import json

import httpx
import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.slack import SlackOutbox

from .conftest import queued_turns
from .test_task_control import route, submit


def commit(company, project, say, status='complete', **actions):
    turn = queued_turns(company, project['project_id'])[0]
    assert company.prepare_turn(turn)['state'] == 'ready'
    response = ProviderResponse(request_id=turn, decision=AgentDecision(say=say, status=status, **actions))
    company.commit_turn(turn, response)
    assert company.commit_turn(turn, response)['duplicate']


def messages(company):
    with company.db.transaction() as conn:
        return conn.execute('SELECT text FROM outbox ORDER BY created_at,id').fetchall()


@pytest.mark.parametrize(('agent', 'status', 'actions', 'expected'), [
    ('director', 'complete', {}, True),
    ('director', 'continue', {}, False),
    ('director', 'wait', {'delegations': [{'agent': 'data', 'instruction': 'Inspect'}]}, False),
    ('data', 'complete', {}, False),
])
def test_only_director_final_mentions_owner(company, agent, status, actions, expected):
    project = company.ingest(event_key='mention', owner='UHUMAN', text='Work', agent=agent,
                             channel='CQUANT', thread_ts='1.1')
    commit(company, project, '<@UHUMAN> Answer <@UOTHER> <!here> <!channel> <!everyone> <!subteam^S123>',
           status, **actions)
    posted = messages(company)
    assert sum('<@UHUMAN>' in row['text'] for row in posted) == int(expected)
    assert all(not any(token in row['text'] for token in ['<@UOTHER>', '<!here>', '<!channel>',
                                                         '<!everyone>', '<!subteam^']) for row in posted)
    assert len(posted) == 1 + len(actions.get('delegations', []))  # Replay never creates another ping.


async def test_progress_stays_quiet_after_completion_and_rate_limit_retries_same_notification(company, credentials):
    project = submit(company, 'first', 'Work')
    commit(company, project, 'Checking', 'continue')
    commit(company, project, 'Ready. <https://example.com|Evidence>')
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 2:
            return httpx.Response(429, headers={'retry-after': '1'})
        return httpx.Response(200, json={'ok': True, 'ts': '200.1'})

    outbox = SlackOutbox(company, credentials, httpx.MockTransport(respond))
    await outbox.send_one()  # The task is already complete; this is still just progress.
    await outbox.send_one()
    with company.db.transaction() as conn:
        conn.execute("UPDATE outbox SET next_at=now()-interval '1 second'")
    await outbox.send_one()
    assert '<@' not in calls[0]['text']
    assert calls[1] == calls[2]
    assert calls[1]['text'].count('<@UHUMAN>') == 1
    assert '<https://example.com|Evidence>' in calls[1]['text']
    assert not calls[1].get('link_names')


def test_clarification_and_blocker_notify_but_status_and_control_ack_do_not(company):
    first = submit(company, 'first', 'ETF comparison')
    question = submit(company, 'unclear', '그건 빼줘', first['project_id'])
    route(company, question, 'clarify', question='어떤 항목을 제외할까요?')
    assert messages(company)[0]['text'].startswith('<@UHUMAN>\n어떤 항목')
    submit(company, 'status', '상태', first['project_id'], control_action='status')
    submit(company, 'stop', '중단해', first['project_id'], control_action='pause')
    assert all('<@' not in row['text'] for row in messages(company)[1:])
    other = company.ingest(event_key='blocked', owner='UHUMAN', text='Work', channel='CQUANT', thread_ts='2.2')
    turn = queued_turns(company, other['project_id'])[0]
    company.block_turn(turn, 'operator_required')
    company.block_turn(turn, 'operator_required')
    assert len(messages(company)) == 4
    assert messages(company)[-1]['text'].startswith('<@UHUMAN>\n업무가 확인 대기')


def test_artifact_only_final_is_delivered_with_owner_mention(company):
    project = submit(company, 'first', 'Work')
    commit(company, project, '', artifacts=[{'title': 'Result', 'content': 'Saved conclusion'}])
    assert messages(company) == [{'text': '<@UHUMAN>\nResult\nSaved conclusion'}]


def test_only_the_owner_of_this_thread_is_notified(company):
    company.settings.slack_allowed_users = ['UHUMAN', 'UOTHER']
    for index, owner in enumerate(['UHUMAN', 'UOTHER', 'UREVOKED', 'operator']):
        project = company.ingest(event_key=f'owner-{index}', owner=owner, text='Work',
                                 channel='CQUANT', thread_ts=f'{index}.1')
        commit(company, project, 'Result')
    assert [row['text'] for row in messages(company)] == [
        '<@UHUMAN>\nResult', '<@UOTHER>\nResult', 'Result', 'Result']


def test_child_director_result_and_peer_messages_do_not_notify_owner(company):
    project = company.ingest(event_key='parent', owner='UHUMAN', text='Work', agent='data',
                             channel='CQUANT', thread_ts='1.1')
    commit(company, project, '', 'wait', delegations=[{'agent': 'director', 'instruction': 'Explain'}])
    commit(company, project, 'Child result', messages=[{'agent': 'data', 'text': '<@UHUMAN> For parent'}])
    assert all('<@' not in row['text'] for row in messages(company))
