"""Load only explicitly mounted secret files; never log their content."""

import os
import sys
from pathlib import Path
from urllib.parse import quote


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("An executable is required")
    for name in ("OPERATOR_TOKEN", "MODEL_RUNTIME_TOKEN", "TEMPORAL_API_KEY", "RESEARCH_WORKER_TOKEN"):
        path = os.environ.pop(f"{name}_FILE", None)
        if path:
            value = Path(path).read_text().strip()
            if not value or value.startswith("REPLACE_"):
                raise SystemExit(f"Configure {name}_FILE before starting")
            os.environ[name] = value
    password_file = os.environ.pop("DATABASE_PASSWORD_FILE", None)
    if password_file:
        password = Path(password_file).read_text().strip()
        if not password:
            raise SystemExit("Database password is empty")
        user = quote(os.environ.get("DATABASE_USER", "company"), safe="")
        database = quote(os.environ.get("DATABASE_NAME", "quant_company"), safe="")
        os.environ["DATABASE_URL"] = (
            f"postgresql://{user}:{quote(password, safe='')}@postgres:5432/{database}"
        )
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
