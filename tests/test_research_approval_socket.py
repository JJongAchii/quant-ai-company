"""Authenticated Socket Mode callback fixtures; no Slack network connections."""

import json
from types import SimpleNamespace

import pytest

from quant_company.cli import manifests
from quant_company.slack import SlackIngress
from quant_company.socket_mode import accept_envelope

from .test_research_approvals import deliver_approval, interaction
from .test_research_store import request, row_for


async def test_socket_interaction_ack_after_commit_and_redelivery(research, credentials):
    _, row = request(research)
    body = await deliver_approval(research, credentials)
    payload = interaction(research, credentials, body)
    envelope = SimpleNamespace(type='interactive', envelope_id='fixture-envelope', payload=payload)
    acked = []

    class Client:
        async def send_socket_mode_response(self, response):
            assert row_for(research, row['id'])['state'] == 'queued'
            acked.append(response.envelope_id)

    for _ in range(2):
        ingress = SlackIngress(research.settings, research, credentials)
        await accept_envelope(ingress, 'director', Client(), envelope)
    assert acked == ['fixture-envelope', 'fixture-envelope']
    with research.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_approved'").fetchone()['n'] == 1


async def test_socket_interaction_transient_failure_no_ack_and_policy_rejection_ack(research, credentials, monkeypatch):
    _, row = request(research)
    body = await deliver_approval(research, credentials)
    envelope = SimpleNamespace(type='interactive', envelope_id='fixture-retry',
                               payload=interaction(research, credentials, body))
    acked = []

    class Client:
        async def send_socket_mode_response(self, response):
            acked.append(response.envelope_id)

    ingress = SlackIngress(research.settings, research, credentials)
    with monkeypatch.context() as scoped:
        def unavailable(**kwargs):
            raise ConnectionError('fixture database offline')
        scoped.setattr(research, 'ingest', unavailable)
        await accept_envelope(ingress, 'director', Client(), envelope)
    assert not acked
    assert row_for(research, row['id'])['state'] == 'pending_approval'
    envelope.payload['api_app_id'] = 'AOTHER'
    await accept_envelope(ingress, 'director', Client(), envelope)
    assert acked == ['fixture-retry']
    assert row_for(research, row['id'])['state'] == 'pending_approval'


@pytest.mark.parametrize('transport', ['socket', 'http'])
def test_director_interactivity_uses_existing_transport_and_other_apps_unchanged(company, tmp_path, transport):
    manifests(company, 'https://fixture.example', tmp_path, transport=transport)
    director = json.loads((tmp_path / 'director.json').read_text())['settings']
    assert director['interactivity']['is_enabled'] is True
    if transport == 'socket':
        assert director['socket_mode_enabled'] is True
        assert 'request_url' not in director['interactivity']
    else:
        assert director['interactivity']['request_url'] == director['event_subscriptions']['request_url']
    researcher = json.loads((tmp_path / 'researcher_kr.json').read_text())['settings']
    assert researcher['interactivity'] == {'is_enabled': False}
