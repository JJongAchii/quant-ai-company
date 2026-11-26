import ssl
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest

from quant_company.market_desk import identity

ENDPOINT = "test.credentials.iot.ap-northeast-2.amazonaws.com"


def test_concurrent_starts_and_restart_share_one_worker_identity(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as threads:
        contexts = list(threads.map(identity.client_context, [tmp_path, tmp_path]))
    assert all(isinstance(context, ssl.SSLContext) for context in contexts)
    key, cert = (tmp_path / "client" / name for name in ("key.pem", "certificate.pem"))
    public = cert.read_bytes()
    assert public.startswith(b"-----BEGIN CERTIFICATE-----") and b"PRIVATE KEY" not in public
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    identity.client_context(tmp_path)
    assert cert.read_bytes() == public
    key.unlink()
    with pytest.raises(FileNotFoundError):
        identity.client_context(tmp_path)
    assert cert.read_bytes() == public


def test_certificate_exchange_uses_only_the_aws_role_alias_and_includes_the_session_token(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="endpoint"):
        identity.CertificateProvider("example.org", tmp_path)
    assert not (tmp_path / "client").exists()
    provider = identity.CertificateProvider(ENDPOINT, tmp_path)
    client_class = httpx.Client
    responses = [httpx.Response(403), httpx.Response(200, json={"credentials": {
        "accessKeyId": "fixture-id", "secretAccessKey": "fixture-secret", "sessionToken": "fixture-session",
        "expiration": "2027-01-01T12:00:00Z",
    }})]

    def respond(request):
        assert str(request.url) == f"https://{ENDPOINT}/role-aliases/insight-invest-market-desk/credentials"
        return responses.pop(0)

    def client(**options):
        assert options["verify"] is provider.context and options["trust_env"] is False
        return client_class(transport=httpx.MockTransport(respond), **options)

    monkeypatch.setattr(identity.httpx, "Client", client)
    with pytest.raises(httpx.HTTPStatusError):
        provider.refresh()
    assert provider.refresh() == {"access_key": "fixture-id", "secret_key": "fixture-secret",
                                  "token": "fixture-session", "expiry_time": "2027-01-01T12:00:00Z"}


def test_s3_signer_refreshes_the_certificate_credentials_even_with_ambient_keys(tmp_path, monkeypatch):
    pytest.importorskip("botocore", reason="The production lake extra supplies the S3 SDK")
    from quant_company.market_desk.runner import BoardStorage

    now = datetime.now(UTC)
    provider = identity.CertificateProvider(ENDPOINT, tmp_path)
    credentials = [{"access_key": f"fixture-{version}", "secret_key": "fixture-secret",
                    "token": f"session-{version}", "expiry_time": (now + duration).isoformat()}
                   for version, duration in (("old", timedelta(minutes=1)), ("new", timedelta(hours=1)))]
    refresh = Mock(side_effect=credentials)
    monkeypatch.setattr(provider, "refresh", refresh)
    monkeypatch.setattr(identity, "CertificateProvider", lambda endpoint: provider)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-fixture")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-secret")
    storage = BoardStorage("test-bucket", ENDPOINT)
    url = storage.s3.generate_presigned_url("get_object", Params={"Bucket": "test-bucket", "Key": "test"})
    assert refresh.call_count == 2 and "fixture-new" in url and "session-new" in url
