"""Run the qualified candidate image on the existing isolated news account path."""

import fcntl
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path('/opt/quant-company/operator-releases/trend-feed-ten-20261006')
BASE_ROOT = ROOT.parent / 'trend-feed-20261006'

if __name__ == '__main__':
    spec = importlib.util.spec_from_file_location('trend_operator', BASE_ROOT / 'production-cutover.py')
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    with (operator.STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reader = operator.inspect()['news-worker']
        candidate = json.loads((operator.STATE / 'releases/trend-feed-ten-20261006-image.json').read_text())
        if reader['Id'] != candidate['target_ids']['news-worker'] or reader['Image'] != candidate['base_image']:
            raise RuntimeError('preview_runtime_changed_requires_review')
        action = sys.argv[1]
        if action not in {'prepare', 'inspect'}:
            raise RuntimeError('unsupported_preview_action')
        overlay = operator.STATE / 'releases/trend-feed-ten-preview.compose.json'
        result = operator.run([*operator.compose(reader, overlay), 'run', '--rm', '--no-deps', '--pull', 'never',
                               '-T', 'news-worker', 'python', '-', action],
                              input=(ROOT / 'preview.py').read_text(), timeout=900)
        preview = json.loads(result)
        if preview.get('valid'):
            if preview['source_sha256'] != candidate['source_sha256'] or preview['cards'] != 10:
                raise RuntimeError('candidate_source_or_topic_count_mismatch')
            operator.atomic(operator.STATE / 'releases/trend-feed-ten-20261006-preview.json',
                            (json.dumps(preview, indent=2, ensure_ascii=False)+'\n').encode())
        print(result)
