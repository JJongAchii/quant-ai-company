"""The committed CLI release contract, shared by installation and runtime gates."""

import json
from importlib.resources import files

RELEASE = json.loads(files(__package__).joinpath("codex_release.json").read_text())
SUPPORTED_CLI_VERSION = RELEASE["version"]
