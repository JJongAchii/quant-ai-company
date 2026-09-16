import asyncio
import json
import logging
import re
from types import SimpleNamespace

import pytest

from quant_company.cli import manifests
from quant_company.slack import SlackIngress
from quant_company.socket_mode import RedactSlackSecrets, accept_envelope, socket_main


def envelope(credentials, **event_changes):
    event = {"type": "app_mention", "user": "UHUMAN", "channel": "CQUANT", "ts": "100.001",
             "text": "<@UBOT0> 검토해 주세요."}
    event.update(event_changes)
    return SimpleNamespace(type="events_api", envelope_id="envelope-1", payload={
        "type": "event_callback", "team_id": "TTEST", "api_app_id": credentials["director"]["app_id"],
        "event_id": "EvSocket", "event": event})


async def test_socket_ack_is_after_database_commit_and_reconnect_redelivery_is_idempotent(company, credentials):
    ingress = SlackIngress(company.settings, company, credentials)
    acked = []

    class Client:
        async def send_socket_mode_response(self, response):
            assert len(company.list_projects("UHUMAN")) == 1
            acked.append(response.envelope_id)

    request = envelope(credentials)
    await accept_envelope(ingress, "director", Client(), request)
    await accept_envelope(SlackIngress(company.settings, company, credentials), "director", Client(), request)
    state = company.project_state(company.list_projects("UHUMAN")[0]["id"])
    assert acked == ["envelope-1", "envelope-1"]
    assert len(state["tasks"]) == 1


async def test_socket_database_failure_is_not_acknowledged_but_policy_rejection_is(company, credentials, monkeypatch):
    ingress = SlackIngress(company.settings, company, credentials)
    acked = []

    class Client:
        async def send_socket_mode_response(self, response):
            acked.append(response.envelope_id)

    with monkeypatch.context() as scoped:
        def unavailable(**kwargs):
            raise ConnectionError("Database offline")
        scoped.setattr(company, "ingest", unavailable)
        await accept_envelope(ingress, "director", Client(), envelope(credentials))
    assert not acked
    unauthorized = envelope(credentials)
    unauthorized.payload["team_id"] = "TOTHER"
    await accept_envelope(ingress, "director", Client(), unauthorized)
    assert acked == ["envelope-1"]
    assert company.list_projects() == []


async def test_socket_lifecycle_closes_all_clients_on_stop_and_connect_failure(company, credentials):
    for item in credentials.values():
        item["app_token"] = "xapp-test-token"
    clients = []

    class Client:
        fail = False

        def __init__(self, **kwargs):
            self.socket_mode_request_listeners = []
            self.closed = False
            clients.append(self)

        async def connect(self):
            if self.fail:
                raise ConnectionError("sensitive SDK failure")

        async def close(self):
            self.closed = True

    stop = asyncio.Event()
    stop.set()
    await socket_main(company.settings, company=company, credentials=credentials, client_factory=Client, stop=stop)
    assert len(clients) == 4 and all(client.closed for client in clients)
    Client.fail = True
    with pytest.raises(RuntimeError, match="could not stay connected"):
        await socket_main(company.settings, company=company, credentials=credentials, client_factory=Client, stop=stop)
    assert all(client.closed for client in clients)


def test_default_manifests_need_no_domain_and_allow_direct_messages(company, tmp_path):
    manifests(company, None, tmp_path)
    data = json.loads((tmp_path / "director.json").read_text())
    assert data["settings"]["socket_mode_enabled"]
    assert "request_url" not in data["settings"]["event_subscriptions"]
    assert data["features"]["app_home"]["messages_tab_read_only_enabled"] is False
    manifests(company, "https://example.com", tmp_path, "http")
    http = json.loads((tmp_path / "director.json").read_text())
    assert not http["settings"]["socket_mode_enabled"]
    assert http["settings"]["event_subscriptions"]["request_url"].endswith("/slack/events/director")


def test_sdk_logger_does_not_expose_tokens_or_socket_tickets():
    record = logging.LogRecord("slack", logging.ERROR, "", 1,
                               "Failed %s wss://slack.example/path?ticket=secret", ("xapp-123-secret",), None)
    assert RedactSlackSecrets().filter(record)
    assert "secret" not in record.getMessage()


def test_manifest_bot_names_obey_slack_character_rules_with_korean_role_names(test_roles, tmp_path):
    # Slack rejects Unicode and spaces in features.bot_user.display_name.
    # https://docs.slack.dev/reference/app-manifest/
    test_roles["director"].name = "총괄"
    manifests(SimpleNamespace(roles=test_roles), None, tmp_path)
    for path in tmp_path.glob("*.json"):
        manifest = json.loads(path.read_text())
        assert re.fullmatch(r"[a-z0-9_.-]{1,80}", manifest["features"]["bot_user"]["display_name"])
    assert json.loads((tmp_path / "director.json").read_text())["display_information"]["name"] == "Quant 총괄"
