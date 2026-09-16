#!/usr/bin/env python3
"""Print a resource plan by default. --apply creates billable AWS resources."""

import argparse
import ipaddress
import json
import re
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--zone", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--stack", default="quant-company")
    parser.add_argument("--key-pair", required=True)
    parser.add_argument("--ssh-cidr", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--blueprint", default="ubuntu_24_04")
    parser.add_argument("--bundle", default="medium_3_0")
    parser.add_argument("--enable-https-ingress", action="store_true",
                        help="Explicitly open public 80/443 for optional HTTPS transport")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cidr = ipaddress.ip_network(args.ssh_cidr, strict=True)
    if cidr.version != 4 or cidr.prefixlen != 32:
        parser.error("--ssh-cidr must be the operator's single IPv4 /32")
    if not re.fullmatch(r"[0-9]{12}", args.account_id):
        parser.error("--account-id must have 12 digits")
    if not re.fullmatch(re.escape(args.region) + "[a-z]", args.zone):
        parser.error("--zone must belong to --region")
    command = [
        "aws", "--profile", args.profile, "--region", args.region, "cloudformation", "deploy",
        "--template-file", str(Path(__file__).with_name("lightsail.json")),
        "--stack-name", args.stack, "--no-fail-on-empty-changeset", "--parameter-overrides",
        f"AvailabilityZone={args.zone}", f"BlueprintId={args.blueprint}", f"BundleId={args.bundle}",
        f"KeyPairName={args.key_pair}", f"SshCidr={args.ssh_cidr}", f"BackupBucketName={args.bucket}",
        f"EnableHttpsIngress={str(args.enable_https_ingress).lower()}",
    ]
    print(json.dumps({
        "mode": "apply" if args.apply else "preview-only",
        "expected_account": args.account_id,
        "aws_profile": args.profile,
        "creates": ["one Lightsail instance", "one static IPv4", "one private S3 backup bucket"],
        "exposes": ["22/tcp only from operator /32"]
        + (["80/tcp and 443/tcp public"] if args.enable_https_ingress else []),
        "https_ingress_enabled": args.enable_https_ingress,
        "retains_on_delete": ["instance", "static IP", "backup bucket"],
        "command_argv": command,
        "before_apply": "Verify regional blueprint/bundle and current price. No account call occurs in preview.",
    }, indent=2))
    if not args.apply:
        return
    account = subprocess.check_output(
        ["aws", "--profile", args.profile, "--region", args.region, "sts", "get-caller-identity",
         "--query", "Account", "--output", "text"],
        text=True,
    ).strip()
    if account != args.account_id:
        raise SystemExit("AWS account mismatch; no resources created")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
