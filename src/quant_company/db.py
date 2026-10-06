from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as conn:
            yield conn

    def migrate(self) -> None:
        with self.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350219)")
            exists = conn.execute("SELECT to_regclass('public.schema_version') AS name").fetchone()
            if exists["name"]:
                versions = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
                if versions != [{"version": 1}]:
                    raise RuntimeError("Unrecognized database schema version; explicit migration required")
            else:
                conn.execute(files("quant_company").joinpath("schema.sql").read_text())
            conn.execute(files("quant_company").joinpath("state_schema.sql").read_text())
            conn.execute(files("quant_company").joinpath("account_schema.sql").read_text())
            conn.execute(files("quant_company").joinpath("model_policy_schema.sql").read_text())
            conn.execute(files("quant_company.staff").joinpath("schema.sql").read_text())
            conn.execute(files("quant_company.news").joinpath("schema.sql").read_text())
            conn.execute(files("quant_company.tech_feed").joinpath("schema.sql").read_text())
            conn.execute(files("quant_company.housing_feed").joinpath("schema.sql").read_text())
            conn.execute(files("quant_company.quant_feed").joinpath("schema.sql").read_text())
            conn.execute(files("quant_company.research").joinpath("schema.sql").read_text())
            conn.execute(files("quant_company.research").joinpath("mission_schema.sql").read_text())
            conn.execute(files("quant_company.research").joinpath("controller_schema.sql").read_text())
            conn.execute(files("quant_company.research").joinpath("program_schema.sql").read_text())
            conn.execute(files("quant_company.data_watch").joinpath("schema.sql").read_text())

    def health(self) -> bool:
        with self.transaction() as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
