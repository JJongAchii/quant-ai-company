"""Qualify five reviewed policy files against the actual active image; do not replace services."""

import datetime
import fcntl
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path('/opt/quant-company/operator-releases/trend-feed-ten-20261006')
BASE_ROOT = ROOT.parent / 'trend-feed-20261006'
PACKAGE = '/opt/company/src/quant_company'
FILES = ('contracts.py', 'editor.py', 'runner.py', 'store.py', 'supplement.py')
REVIEWED_BEFORE = {
    'trend_feed/contracts.py': 'fb4f5fa2862f539d1e72417ca46052cf684d44cf68d6eeed7abaae052359e5d8',
    'trend_feed/runner.py': '7dbde67a9eed601e606e8287b78d3efa2854883b605ae7042a5b58260e7a0226',
    'trend_feed/supplement.py': None,
    'trend_feed/editor.py': '62738b83711e9485852644984b0376dcfdb821b51dfbeb40f89b9277b73ad137',
    'trend_feed/store.py': '300f7f4b3a44a57816007ada5ef51bf91417304a27f9ddde35df64065a256bc2',
}


if __name__ == '__main__':
    spec = importlib.util.spec_from_file_location('trend_operator', BASE_ROOT / 'production-cutover.py')
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    with (operator.STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = operator.inspect()
        bases = {before[name]['Image'] for name in operator.TARGETS}
        if len(bases) != 1:
            raise RuntimeError('consumer_images_differ')
        base = bases.pop()
        commit = sys.argv[1]
        if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
            raise RuntimeError('commit_not_frozen')
        base_tag = 'quant-company-trend-base:' + base.split(':', 1)[1][:16]
        operator.run(['docker', 'tag', base, base_tag])
        context = ROOT / 'image'
        context.mkdir(exist_ok=True)
        for name in FILES:
            operator.atomic(context / name, (ROOT / name).read_bytes(), mode=0o644)
        dockerfile = (f'FROM {base_tag}\nUSER root\nCOPY contracts.py editor.py runner.py store.py supplement.py {PACKAGE}/trend_feed/\n'
                      f'RUN chmod 644 {PACKAGE}/trend_feed/*.py && '
                      f"python -c 'from pathlib import Path; [p.unlink() for p in Path(\"{PACKAGE}/trend_feed/__pycache__\").glob(\"*.pyc\")]'\n"
                      'USER 10001:10001\n'
                      f'LABEL io.quant-company.trend-feed={commit}\n'
                      f'LABEL io.quant-company.trend-feed.base-id={base}\n')
        operator.atomic(context / 'Dockerfile', dockerfile.encode(), mode=0o644)
        tag = 'quant-company-trend:' + commit + '-ten'
        operator.run(['env', 'DOCKER_BUILDKIT=0', 'docker', 'build', '--network', 'none', '--pull=false',
                      '--tag', tag, str(context)], timeout=180)
        image = json.loads(operator.run(['docker', 'image', 'inspect', tag]))[0]
        parent = json.loads(operator.run(['docker', 'image', 'inspect', base]))[0]
        config_preserved = all(image['Config'].get(k) == parent['Config'].get(k)
                               for k in ('Env', 'Cmd', 'Entrypoint', 'User', 'WorkingDir', 'Healthcheck'))
        inventory_code = ('import hashlib,json; from pathlib import Path; '
                          f"root=Path('{PACKAGE}'); print(json.dumps({{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
                          "for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))")

        def inventory(identifier):
            return json.loads(operator.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                                            '--security-opt', 'no-new-privileges:true', '--entrypoint', 'python',
                                            identifier, '-c', inventory_code]))

        old, new = inventory(base), inventory(image['Id'])
        if any(old.get(k) != v for k, v in REVIEWED_BEFORE.items()):
            raise RuntimeError('policy_sources_changed_requires_review')
        changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        expected = {f'trend_feed/{name}': hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}
        if not config_preserved or changed != sorted(expected) or any(new[k] != v for k, v in expected.items()):
            raise RuntimeError('unqualified_policy_delta')
        result = {'checked_at': datetime.datetime.now(datetime.UTC).isoformat(), 'commit': commit,
                  'base_image': base, 'image': image['Id'], 'image_tag': tag, 'base_config_preserved': config_preserved,
                  'changed_files': changed, 'source_file_count': len(new), 'source_sha256': expected,
                  'before_sha256': {k: old[k] for k in expected}, 'running_services_changed': False,
                  'target_ids': {n: before[n]['Id'] for n in operator.TARGETS},
                  'target_signature_sha256': {n: hashlib.sha256(json.dumps(operator.signature(before[n]), sort_keys=True).encode()).hexdigest()
                                              for n in operator.TARGETS}}
        override = operator.STATE / 'config/trend-feed-20261006-news-worker.compose.json'
        candidate_override = operator.STATE / 'releases/trend-feed-ten-preview.compose.json'
        content = json.loads(override.read_text())
        content['services']['news-worker']['image'] = tag
        content['services']['news-worker']['environment'] = dict(e.split('=', 1) for e in before['news-worker']['Config']['Env'])
        operator.atomic(candidate_override, json.dumps(content).encode())
        operator.atomic(operator.STATE / 'releases/trend-feed-ten-20261006-image.json', (json.dumps(result, indent=2) + '\n').encode())
        print(json.dumps({'image': result}, ensure_ascii=False))
