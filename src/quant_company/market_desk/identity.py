"""Exchange the publisher's own TLS certificate for expiring AWS credentials."""

import errno
import re
import ssl
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

IDENTITY_ROOT = Path("/var/lib/market-desk/identity")
ROLE_ALIAS = "insight-invest-market-desk"


def client_context(root):
    identity = root / "client"
    if not identity.is_dir():
        # Publish the complete pair atomically. Concurrent starts use the identity that won the rename.
        with TemporaryDirectory(dir=root) as tmp:
            stage = Path(tmp) / "client"
            stage.mkdir(mode=0o700)
            key = stage / "key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "730",
                "-subj", "/CN=insight-invest-market-desk", "-addext", "basicConstraints=critical,CA:FALSE",
                "-addext", "keyUsage=critical,digitalSignature", "-addext", "extendedKeyUsage=clientAuth",
                "-keyout", str(key), "-out", str(stage / "certificate.pem"),
            ], check=True, timeout=30, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            key.chmod(0o600)
            try:
                stage.rename(identity)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
    context = ssl.create_default_context()
    context.load_cert_chain(identity / "certificate.pem", identity / "key.pem")
    return context


class CertificateProvider:
    METHOD = "market-desk-certificate"

    def __init__(self, endpoint, root=IDENTITY_ROOT):
        if not re.fullmatch(r"[a-z0-9-]+\.credentials\.iot\.ap-northeast-2\.amazonaws\.com", endpoint):
            raise ValueError("Invalid Seoul IoT credential provider endpoint")
        self.url = f"https://{endpoint}/role-aliases/{ROLE_ALIAS}/credentials"
        self.context = client_context(root)

    def refresh(self):
        with httpx.Client(verify=self.context, trust_env=False, timeout=8) as client:
            response = client.get(self.url)
            response.raise_for_status()
            creds = response.json()["credentials"]
        return {"access_key": creds["accessKeyId"], "secret_key": creds["secretAccessKey"],
                "token": creds["sessionToken"], "expiry_time": creds["expiration"]}

    def load(self):
        from botocore.credentials import RefreshableCredentials

        return RefreshableCredentials.create_from_metadata(
            metadata=self.refresh(), refresh_using=self.refresh, method=self.METHOD,
        )
