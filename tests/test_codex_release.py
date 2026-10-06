"""Release detection cannot promote an unstable or unrelated package into the runtime."""

import importlib.util
import json
from pathlib import Path

import pytest

from quant_company.providers.codex_release import SUPPORTED_CLI_VERSION

spec = importlib.util.spec_from_file_location('detect_codex_release', Path('scripts/detect_codex_release.py'))
detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detector)


def metadata(version='0.161.0'):
    return {'name': '@openai/codex', 'version': version, 'dist': {'integrity': 'sha512-' + 'A' * 86 + '=='}}


def test_candidate_detection_leaves_the_qualified_contract_unchanged():
    pin = Path('src/quant_company/providers/codex_release.json')
    before = pin.read_bytes()
    found = detector.candidate('0.160.1', metadata())
    assert found['candidate_version'] == '0.161.0' and found['state'] == 'unqualified'
    assert found['current_validated_version'] == '0.160.1'
    assert pin.read_bytes() == before
    assert SUPPORTED_CLI_VERSION == json.loads(before)['version']


@pytest.mark.parametrize('value', ['0.160.1', '0.154.0'])
def test_same_or_older_release_is_not_an_upgrade(value):
    assert detector.candidate('0.160.1', metadata(value)) is None


@pytest.mark.parametrize('value', ['0.161.0-alpha.1', 'latest', '0.161.0;echo unsafe', '', None])
def test_stable_versions_only(value):
    with pytest.raises(ValueError, match='stable_cli_version_required'):
        detector.candidate('0.160.1', metadata(value))


def test_unrelated_distribution_or_missing_integrity_is_rejected():
    row = metadata()
    row['name'] = '@other/codex'
    with pytest.raises(ValueError, match='official_package_identity_required'):
        detector.candidate('0.160.1', row)
    row = metadata()
    row['dist']['integrity'] = 'unreviewed'
    with pytest.raises(ValueError, match='distribution_integrity_required'):
        detector.candidate('0.160.1', row)
