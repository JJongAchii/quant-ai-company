import importlib.util
import io
import tarfile
from pathlib import Path

import pytest


def release():
    path = Path(__file__).parents[1] / "deploy/housing_rental_release.py"
    spec = importlib.util.spec_from_file_location("housing_rental_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["../escape", "src/quant_company/other_module.py"])
def test_release_bundle_rejects_files_outside_housing_package(tmp_path, name):
    bundle = tmp_path / "bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        data = b"unexpected"
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="housing_bundle_member_invalid"):
        release().unpack(bundle, tmp_path / "source")
    assert not (tmp_path / "escape").exists()
