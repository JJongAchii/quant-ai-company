"""Skip an older scheduled release executor while a reviewed image cohort is active.

Used as systemd ExecCondition, so status 1 skips the release without failing it.
Maintenance observation and PR preparation continue. A full operator release must
reconcile the per-service source/image manifest before removing this condition.
"""

import sys
from pathlib import Path


def main():
    path = Path(sys.argv[1])
    enabled = any(line.strip() == "MODEL_ASSIGNMENTS_ENABLED=true" for line in path.read_text().splitlines())
    if enabled:
        print("Model assignment image cohort active; scheduled code cutover requires operator integration.")
    return 1 if enabled else 0


if __name__ == "__main__":
    raise SystemExit(main())
