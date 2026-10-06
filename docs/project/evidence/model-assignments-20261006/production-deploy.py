"""Hold the host deployment lock through stage, real catalog probe and cutover."""

import fcntl
import importlib.util
import os
import pathlib
import runpy
import sys

STATE = pathlib.Path("/var/lib/quant-company")
ROOT = pathlib.Path("/opt/quant-company/operator-releases/model-assignments-20261006")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


if __name__ == "__main__":
    os.umask(0o077)
    scripts = pathlib.Path(sys.argv[4])
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stage = module("assignment_stage", scripts / "production-stage.py")
        stage.stage(pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3])
        runpy.run_path(str(scripts / "production-catalog-probe.py"))
        cutover = module("assignment_cutover", scripts / "production-cutover.py")
        cutover.atomic(ROOT / "backup-empty.compose.json", b'{"services":{}}')
        cutover.cutover()
