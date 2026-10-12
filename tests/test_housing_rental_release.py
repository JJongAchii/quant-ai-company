import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

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


def test_activation_preserves_services_from_cutover_snapshot_after_unrelated_stage_drift(tmp_path, monkeypatch):
    module = release()
    commit = "a" * 40
    bases = {name: {"container_id": name} for name in module.SERVICES}
    images = {name: {"tag": name + "-new", "id": name + "-image"} for name in module.SERVICES}
    stage_before = {"unrelated": {"id": "old"}}
    cutover_before = {"unrelated": {"id": "new"}}
    after = {"unrelated": {"id": "new"}, **{
        "quant-company-" + name + "-1": {"running": True, "oom": False, "image_id": images[name]["id"]}
        for name in module.SERVICES}}
    journal = tmp_path / "receipt.json"
    journal.write_text(json.dumps({"phase": "staged", "commit": commit,
        "current_release": str(Path("/opt/quant-company/current").resolve()), "before": stage_before,
        "bases": bases, "images": images, "package": {},
        "overrides": {name: name + ".compose.json" for name in module.SERVICES}}))
    snapshots = iter((cutover_before, after))
    monkeypatch.setattr(module, "inventory", lambda: next(snapshots))
    monkeypatch.setattr(module, "pending", lambda: [0, 0, 0, 0])
    monkeypatch.setattr(module, "service", lambda name: bases[name])
    monkeypatch.setattr(module, "image_package", lambda tag: {})
    monkeypatch.setattr(module, "compose", lambda *args, **kwargs: "")
    monkeypatch.setattr(module, "rental_sources_ready", lambda since: True)

    def collection(command):
        assert command[:5] == ["docker", "exec", "quant-company-housing-feed-worker-1",
                               "python", "/app/entrypoint.py"]
        return '{"state":"idle","sources":[]}'

    monkeypatch.setattr(module, "run", collection)
    assert module.activate(SimpleNamespace(commit=commit), journal)["phase"] == "activated"
    assert json.loads(journal.read_text())["activation_before"]["unrelated"]["id"] == "new"
