#!/usr/bin/env bash
# Run only on the newly provisioned, dedicated Ubuntu 24.04 company host.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root on the company host'; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo 'Expected Ubuntu 24.04'; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=120 update
apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends ca-certificates curl python3 rsync unzip gnupg
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
qcompany_arch=$(dpkg --print-architecture)
cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $qcompany_arch
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get -o DPkg::Lock::Timeout=120 update
apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
# Noble does not ship an awscli apt candidate. Verify the official CLI v2 installer.
if ! command -v aws >/dev/null 2>&1; then
  qcompany_install=$(mktemp -d)
  trap 'rm -rf "$qcompany_install"' EXIT
  install -d -m 0700 "$qcompany_install/gnupg"
  qcompany_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  gpg --homedir "$qcompany_install/gnupg" --batch --import "$qcompany_script_dir/aws-cli-signing-key.asc"
  gpg --homedir "$qcompany_install/gnupg" --with-colons --fingerprint | \
    grep -F 'fpr:::::::::FB5DB77FD5C118B80511ADA8A6310ACC4672475C:'
  case "$qcompany_arch" in
    amd64) qcompany_aws_arch=x86_64 ;;
    arm64) qcompany_aws_arch=aarch64 ;;
    *) echo 'Unsupported AWS CLI architecture'; exit 1 ;;
  esac
  qcompany_zip="https://awscli.amazonaws.com/awscli-exe-linux-$qcompany_aws_arch.zip"
  curl --fail --silent --show-error "$qcompany_zip" -o "$qcompany_install/aws.zip"
  curl --fail --silent --show-error "$qcompany_zip.sig" -o "$qcompany_install/aws.sig"
  gpg --homedir "$qcompany_install/gnupg" --batch --verify "$qcompany_install/aws.sig" "$qcompany_install/aws.zip"
  sha256sum "$qcompany_install/aws.zip"
  unzip -q "$qcompany_install/aws.zip" -d "$qcompany_install"
  "$qcompany_install/aws/install"
fi
docker version --format '{{.Server.Version}}'
docker compose version
aws --version
