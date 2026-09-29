"""Prepare a worker-only role file for the research-pinned company image."""

import argparse
import hashlib
import json
from pathlib import Path


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-source-sha256")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("Source and output must differ")
    original = args.source.read_bytes()
    source_digest = digest(original)
    if args.apply and args.expect_source_sha256 != source_digest:
        parser.error("Source digest changed; inspect the live role configuration again")
    roles = json.loads(original)
    if not isinstance(roles, list):
        parser.error("Role configuration must be a list")
    excluded = [role for role in roles if role.get("id") == "quant_scout"]
    if (len(excluded) != 1 or excluded[0].get("active") is not False
            or excluded[0].get("tools") != [] or excluded[0].get("can_delegate_to") != []):
        parser.error("Only the inactive outbound-only Quant Scout role may be excluded")
    filtered = [role for role in roles if role.get("id") != "quant_scout"]
    rendered = (json.dumps(filtered, ensure_ascii=False, indent=2) + "\n").encode()
    if [role for role in roles if role["active"]] != [role for role in filtered if role["active"]]:
        parser.error("An active employee changed")
    state = "dry_run"
    if args.output.exists():
        if args.output.read_bytes() != rendered:
            parser.error("Output already exists with different content")
        state = "already_prepared"
    elif args.apply:
        with args.output.open("xb") as destination:
            destination.write(rendered)
        state = "created"
    print(json.dumps({"state": state, "source_sha256": source_digest,
                      "output_sha256": digest(rendered), "source_roles": len(roles),
                      "output_roles": len(filtered), "active_roles_preserved": sum(role["active"] for role in filtered)}))


if __name__ == "__main__":
    main()
