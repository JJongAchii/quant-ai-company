import hashlib

import pytest

from quant_company.company import PolicyError
from quant_company.research.builds import experiment_signature


def signature(root, content, code=b"fixed evaluator"):
    (root / "config.json").write_bytes(content)
    files = {"config.json": hashlib.sha256(content).hexdigest(),
             "run.py": hashlib.sha256(code).hexdigest()}
    return experiment_signature(root, files, ["config.json"])


def test_reformatting_a_formula_is_not_an_additional_trial(tmp_path):
    first = signature(tmp_path, b'{"schema_version":1,"variant":"A25"}')
    assert signature(tmp_path, b'{\n "variant": "A25", "schema_version": 1\n}\n') == first
    assert signature(tmp_path, b'{"schema_version":1,"variant":"A75"}') != first
    assert signature(tmp_path, b'{"schema_version":1,"variant":"A25"}', b"repaired evaluator") != first


@pytest.mark.parametrize("content", [
    b'{"variant":"A25","variant":"A75"}', b'{"value":NaN}', b'{"value":Infinity}', b'not JSON',
])
def test_ambiguous_or_non_finite_config_has_no_experiment_identity(tmp_path, content):
    with pytest.raises(PolicyError, match="unambiguous finite JSON"):
        signature(tmp_path, content)
