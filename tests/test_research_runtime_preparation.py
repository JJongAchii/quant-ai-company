"""Offline preparation fixtures; no copied interpreter or scientific code executes."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quant_company.research.sandbox import profile_from_dict, runtime_digest
from scripts import prepare_research_runtime as prep


def elf_bytes(kind=3):
    header = bytearray(20)
    header[:6] = b"\x7fELF\x02\x01"
    header[16:18] = kind.to_bytes(2, "little")
    return bytes(header) + b"SYNTHETIC ELF IDENTITY; NEVER EXECUTE\n"


@pytest.fixture
def source(tmp_path):
    prefix = tmp_path / "trusted-prefix"
    stdlib = prefix / "lib/python3.11"
    site = stdlib / "site-packages"
    site.mkdir(parents=True)
    executable = prefix / "bin/python3.11"
    executable.parent.mkdir()
    executable.write_bytes(elf_bytes())
    executable.chmod(0o755)
    (stdlib / "os.py").write_text("# fixture standard library\n")
    (site / "unauthorized_auth").mkdir()
    (site / "unauthorized_auth/credentials.py").write_text("PRIVATE = 'not copied'\n")
    (site / "startup.pth").write_text("raise RuntimeError('never process .pth')\n")
    for module, distribution in prep.PACKAGES.items():
        if module == "six":
            (site / "six.py").write_text("raise RuntimeError('do not import source modules')\n")
        else:
            package = site / module
            package.mkdir()
            (package / "__init__.py").write_text("raise RuntimeError('do not import source modules')\n")
            (package / "tests").mkdir()
            (package / "tests/private.txt").write_text("not a runtime input")
            (package / "sneaky.pth").write_text("not copied")
        info = site / (distribution.replace("-", "_") + "-1.2.3.dist-info")
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {distribution}\nVersion: 1.2.3\n")
        (info / "WHEEL").write_text("Wheel-Version: 1.0\n")
        (info / "direct_url.json").write_text('{"url":"https://private.example.invalid/token","dir_info":{"editable":false}}')
    libraries = site / "numpy.libs"
    libraries.mkdir()
    (libraries / "libfixture.so").write_bytes(elf_bytes())
    packages = prep.discover_packages((site,))
    return {"implementation": "cpython", "version": "3.11.9", "prefix": str(prefix),
            "executable": str(executable), "stdlib": str(stdlib), "libdir": str(prefix / "lib"),
            "sites": [str(site)], "packages": packages}


def test_real_wheel_metadata_discovery_keeps_the_exact_allowlist_without_imports_or_pth(source):
    packages = prep.discover_packages(tuple(Path(site) for site in source["sites"]))
    assert {item["module"]: item["distribution"] for item in packages} == prep.PACKAGES
    assert {item["version"] for item in packages} == {"1.2.3"}
    assert not any("unauthorized_auth" in json.dumps(item) for item in packages)
    assert next(item for item in packages if item["module"] == "six")["package"].endswith("six.py")
    assert len(next(item for item in packages if item["module"] == "numpy")["libs"]) == 1


@pytest.mark.parametrize("kind", ["editable", "missing", "duplicate", "metadata_escape", "package_escape", "libs_escape"])
def test_discovery_rejects_incomplete_editable_ambiguous_or_escaping_package_sources(source, tmp_path, kind):
    site = Path(source["sites"][0])
    info = site / "numpy-1.2.3.dist-info"
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "editable":
        (info / "direct_url.json").write_text('{"dir_info":{"editable":true}}')
    elif kind == "missing":
        (info / "METADATA").unlink()
    elif kind == "duplicate":
        (site / "numpy-9.9.9.dist-info").mkdir()
    elif kind == "metadata_escape":
        (outside / "metadata").write_text("Name: numpy\nVersion: 1.2.3\n")
        (info / "METADATA").unlink()
        (info / "METADATA").symlink_to(outside / "metadata")
    elif kind == "package_escape":
        (site / "six.py").unlink()
        (outside / "six.py").write_text("private")
        (site / "six.py").symlink_to(outside / "six.py")
    else:
        (site / "numpy.libs/libfixture.so").unlink()
        (site / "numpy.libs").rmdir()
        (site / "numpy.libs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(prep.RuntimePreparationError):
        prep.discover_packages((site,))


def test_probe_uses_only_trusted_elf_and_isolated_no_site_python_flags(source, monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps({key: value for key, value in source.items() if key != "packages"}))

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not inherited")
    monkeypatch.setattr(prep.subprocess, "run", run)
    discovered = prep.probe_interpreter(Path(source["executable"]))
    assert discovered["packages"] == source["packages"]
    assert seen["argv"][1:4] == ["-I", "-S", "-c"]
    assert "AWS_SECRET_ACCESS_KEY" not in seen["env"]
    assert "PYTHONPATH" not in seen["env"] and seen["env"]["HOME"] == "/nonexistent"
    assert seen["cwd"] == "/" and seen["timeout"] == 30


def test_script_or_non_elf_interpreter_is_rejected_before_any_subprocess(tmp_path, monkeypatch):
    wrapper = tmp_path / "python"
    wrapper.write_text("#!/bin/sh\necho forbidden\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(prep.subprocess, "run", lambda *args, **kwargs: pytest.fail("wrapper executed"))
    with pytest.raises(prep.RuntimePreparationError, match="trusted-linux-python-required"):
        prep.probe_interpreter(wrapper)


def test_ldd_parser_identifies_needed_names_and_actual_loader_paths():
    output = """linux-vdso.so.1 (0x00007fff)
 libpython3.11.so.1.0 => /opt/python/lib/libpython3.11.so.1.0 (0x001000)
 libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x002000)
 /lib64/ld-linux-x86-64.so.2 (0x003000)
"""
    assert prep.parse_ldd(output, 0) == [
        ("libpython3.11.so.1.0", Path("/opt/python/lib/libpython3.11.so.1.0")),
        ("libc.so.6", Path("/lib/x86_64-linux-gnu/libc.so.6")),
        (None, Path("/lib64/ld-linux-x86-64.so.2")),
    ]
    assert prep.parse_ldd("statically linked\n", 0) == []


@pytest.mark.parametrize("output,code", [
    ("libmissing.so => not found\n", 0), ("unrecognized warning\n", 0),
    ("../escape.so => /lib/libm.so (0x0001)\n", 0), ("not a dynamic executable\n", 1),
])
def test_missing_or_ambiguous_elf_dependencies_fail_closed(output, code):
    with pytest.raises(prep.RuntimePreparationError):
        prep.parse_ldd(output, code)


def test_copy_normalizes_only_in_closure_symlinks_and_never_hardlinks_sources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "module.py"
    original.write_text("copied bytes\n")
    original.chmod(0o644)
    (source / "alias.py").symlink_to("module.py")
    target = tmp_path / "target"
    target.mkdir()
    copier = prep.Copier(target, (source,))
    copier.copy_tree(source, target / "package")
    assert not (target / "package/alias.py").is_symlink()
    assert (target / "package/alias.py").read_bytes() == original.read_bytes()
    assert original.stat().st_ino != (target / "package/module.py").stat().st_ino
    assert original.stat().st_mode & 0o222
    assert not (target / "package/module.py").stat().st_mode & 0o222
    original.write_text("source changed concurrently")
    with pytest.raises(prep.RuntimePreparationError, match="source-changed-during-copy"):
        copier.verify_sources()


@pytest.mark.parametrize("kind", ["outside", "cycle", "other-package", "hidden-site-package"])
def test_recursive_copy_rejects_symlink_escape_even_inside_broader_elf_or_stdlib_roots(tmp_path, kind):
    broad = tmp_path / "trusted"
    package = broad / "site-packages/allowed"
    package.mkdir(parents=True)
    other = broad / "site-packages/auth"
    other.mkdir()
    (other / "credentials").write_text("not copied")
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "out"
    destination.mkdir()
    if kind == "outside":
        (package / "link").symlink_to(outside, target_is_directory=True)
    elif kind == "cycle":
        (package / "link").symlink_to(package, target_is_directory=True)
    else:
        (package / "link").symlink_to(other, target_is_directory=True)
    tree_roots = (package, broad) if kind == "hidden-site-package" else (package,)
    copier = prep.Copier(destination, (broad,), tree_roots=tree_roots)
    with pytest.raises(prep.RuntimePreparationError):
        copier.copy_tree(package, destination / "package")


def test_elf_closure_follows_transitive_private_dependencies_and_creates_libpython_alias(tmp_path, monkeypatch):
    source = tmp_path / "approved"
    source.mkdir()
    executable, library, downstream = (source / name for name in ("python", "libpython3.11.so.1.0", "libfixture.so"))
    for path in (executable, library, downstream):
        path.write_bytes(elf_bytes() + path.name.encode())
    destination = tmp_path / "private"
    destination.mkdir()
    copier = prep.Copier(destination, (source,))
    copier.copy_file(executable, destination / "runtime/python")
    copier.copy_file(library, destination / "runtime/lib/libpython3.11.so.1.0")
    copier.copy_file(downstream, destination / "runtime/lib/libfixture.so")
    inspected = []

    def inspect(path, _):
        inspected.append(path)
        return {executable: [(library.name, library)], library: [(downstream.name, downstream)], downstream: []}[path]

    monkeypatch.setattr(prep, "inspect_elf", inspect)
    edges = prep.copy_elf_closure(copier, (source,))
    assert set(inspected) == {executable, library, downstream}
    assert len(edges) == 2
    assert (destination / "elf/lib/libpython3.11.so.1.0").read_bytes() == library.read_bytes()
    assert (destination / "elf/lib/libfixture.so").read_bytes() == downstream.read_bytes()
    assert library.stat().st_ino != (destination / "elf/lib/libpython3.11.so.1.0").stat().st_ino


def test_elf_closure_rejects_unregistered_private_library_and_conflicting_soname(tmp_path, monkeypatch):
    source = tmp_path / "approved"
    source.mkdir()
    binary = source / "python"
    library = source / "liboutside.so"
    binary.write_bytes(elf_bytes())
    library.write_bytes(elf_bytes() + b"different")
    target = tmp_path / "private"
    target.mkdir()
    copier = prep.Copier(target, (source,))
    copier.copy_file(binary, target / "runtime/python")
    monkeypatch.setattr(prep, "inspect_elf", lambda *args: [("liboutside.so", library)])
    with pytest.raises(prep.RuntimePreparationError, match="unregistered-private-elf-library"):
        prep.copy_elf_closure(copier, (source,))
    with pytest.raises(prep.RuntimePreparationError, match="conflicting-runtime-library"):
        copier.copy_file(library, target / "runtime/python")


def test_standalone_python_dependency_directory_does_not_authorize_nested_site_packages(tmp_path, monkeypatch):
    source = tmp_path / "prefix/lib"
    nested = source / "python3.11/site-packages/unapproved"
    nested.mkdir(parents=True)
    binary, library, other = source / "python", source / "libssl.so.3", nested / "private.so"
    for path in (binary, library, other):
        path.write_bytes(elf_bytes() + path.name.encode())
    target = tmp_path / "private"
    target.mkdir()
    copier = prep.Copier(target, (source,))
    copier.copy_file(binary, target / "runtime/python")
    monkeypatch.setattr(prep, "inspect_elf", lambda path, _: [(library.name, library)] if path == binary else [])
    prep.copy_elf_closure(copier, (source,), (source,))
    assert (target / "elf/lib/libssl.so.3").read_bytes() == library.read_bytes()
    monkeypatch.setattr(prep, "inspect_elf", lambda *_: [(other.name, other)])
    with pytest.raises(prep.RuntimePreparationError, match="unregistered-private-elf-library"):
        prep.copy_elf_closure(copier, (source,), (source,))


def test_fixture_packaging_emits_existing_profile_schema_and_byte_bound_version_manifest(source, tmp_path, monkeypatch):
    # Linux discovery/ldd are explicitly fixtures here. The actual filesystem copy,
    # package metadata, hashing and RuntimeProfile consumer are exercised locally.
    monkeypatch.setattr(prep.platform, "system", lambda: "Linux")
    monkeypatch.setattr(prep, "probe_interpreter", lambda _: source)
    monkeypatch.setattr(prep, "inspect_elf", lambda *args: [])
    destination = tmp_path / "runtime-prepared"
    before = prep.sha_file(Path(source["executable"]))
    receipt = prep.prepare_runtime(Path(source["executable"]), destination)
    profile = profile_from_dict(json.loads(Path(receipt["profile"]).read_text()))
    manifest = json.loads(Path(receipt["manifest"]).read_text())
    assert profile.python_executable == "/runtime/python"
    assert profile.profile_id == "kr-etf-monthly-python-v1"
    assert profile.python_sha256 == before == prep.sha_file(Path(source["executable"]))
    assert set(receipt["packages"]) == set(prep.PACKAGES)
    assert manifest["schema_version"] == 1 and "not-run" in manifest["qualification"]
    assert manifest["profile_sha256"] == prep.sha_file(Path(receipt["profile"]))
    assert all(mount.sha256 == runtime_digest(mount.source, profile.allowed_roots) for mount in profile.mounts)
    assert all(not Path(path).is_relative_to(Path(source["sites"][0]) / "unauthorized_auth")
               for path in manifest["source_files"])
    for name, info in manifest["copied_files"].items():
        assert prep.sha_file(destination / name) == info["sha256"]
        assert not (destination / name).is_symlink()
        assert not (destination / name).stat().st_mode & 0o222
        assert not name.endswith((".pth", ".egg-link", "direct_url.json"))
        assert "/tests/" not in name and "unauthorized_auth" not in name
    assert destination.stat().st_mode & 0o777 == 0o700
    assert {mount.target for mount in profile.mounts} == {"/runtime"}
    assert "private.example.invalid" not in json.dumps(manifest)


def test_platform_and_fresh_destination_guards_leave_source_and_existing_directories_untouched(source, tmp_path, monkeypatch):
    executable = Path(source["executable"])
    monkeypatch.setattr(prep.platform, "system", lambda: "Darwin")
    with pytest.raises(prep.RuntimePreparationError, match="linux-runtime-preparation-required"):
        prep.prepare_runtime(executable, tmp_path / "new")
    monkeypatch.setattr(prep.platform, "system", lambda: "Linux")
    monkeypatch.setattr(prep, "probe_interpreter", lambda _: source)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "marker").write_text("preserved")
    with pytest.raises(prep.RuntimePreparationError, match="fresh-private-destination-required"):
        prep.prepare_runtime(executable, existing)
    assert (existing / "marker").read_text() == "preserved"
    with pytest.raises(prep.RuntimePreparationError, match="destination-inside-source-runtime"):
        prep.prepare_runtime(executable, Path(source["prefix"]) / "new-runtime")
    assert not (Path(source["prefix"]) / "new-runtime").exists()
