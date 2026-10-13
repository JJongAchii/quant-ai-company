"""Find a stable official npm release. Candidate metadata never changes the runtime pin."""

import argparse
import json
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REGISTRY = 'https://registry.npmjs.org/@openai%2Fcodex/latest'


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError('stable_cli_version_required')
    return tuple(int(part) for part in value.split('.'))


def candidate(current, release):
    latest = release['version']
    if version(latest) <= version(current):
        return None
    if release['name'] != '@openai/codex' or release['version'] != latest:
        raise ValueError('official_package_identity_required')
    integrity = release['dist']['integrity']
    if not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==', integrity):
        raise ValueError('distribution_integrity_required')
    return {'schema_version': 1, 'package': '@openai/codex', 'current_validated_version': current,
            'candidate_version': latest, 'npm_integrity': integrity, 'state': 'unqualified',
            'changelog': 'https://learn.chatgpt.com/docs/changelog'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('src/quant_company/providers/codex_release.json'))
    parser.add_argument('--output', type=Path, default=Path('deploy/codex-candidate.json'))
    args = parser.parse_args()
    current = json.loads(args.manifest.read_text())['version']
    # No credentials, model calls or dependency execution.
    with urllib.request.urlopen(REGISTRY, timeout=30) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('registry_metadata_too_large')
    found = candidate(current, json.loads(raw))
    if found:
        args.output.write_text(json.dumps(found, indent=2) + '\n')
    print(json.dumps({'checked_at': datetime.now(UTC).isoformat(), 'current': current,
                      'candidate': found['candidate_version'] if found else None}))


if __name__ == '__main__':
    main()
