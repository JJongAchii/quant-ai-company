import hashlib
import time
from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

# Applied in this order on every migration; schema.sql runs only on a database without schema_version.
CORE_SCHEMA = (
    ("quant_company", "state_schema.sql"),
    ("quant_company", "account_schema.sql"),
    ("quant_company", "model_policy_schema.sql"),
    ("quant_company.staff", "schema.sql"),
    ("quant_company.news", "schema.sql"),
    ("quant_company.briefing", "schema.sql"),
    ("quant_company.video", "schema.sql"),
    ("quant_company.tech_feed", "schema.sql"),
    ("quant_company.trend_feed", "schema.sql"),
    ("quant_company.housing_feed", "schema.sql"),
    ("quant_company.quant_feed", "schema.sql"),
    ("quant_company.research", "schema.sql"),
    ("quant_company.research", "mission_schema.sql"),
    ("quant_company.research", "controller_schema.sql"),
    ("quant_company.research", "program_schema.sql"),
    ("quant_company.research", "planning_schema.sql"),
    ("quant_company.data_watch", "schema.sql"),
)
RETRYABLE = (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected)


class Database:
    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as conn:
            yield conn

    def migrate(self, force: bool = False) -> None:
        texts = [files(package).joinpath(name).read_text() for package, name in CORE_SCHEMA]
        self.apply_schema("core", texts, lock=71350219, install=files("quant_company").joinpath("schema.sql").read_text(),
                          force=force)

    def apply_schema(self, name, texts, *, lock, install=None, force=False, lock_timeout="5s", attempts=6, sleep=time.sleep):
        """Run idempotent DDL only when its text changed since the last successful run.

        Even a no-op ALTER TABLE ... IF NOT EXISTS or CREATE INDEX IF NOT EXISTS takes a table lock. A service that
        reran every file on start contended with live queries (2026-10-14 09:27 KST: dispatch deadlocked against a
        starting process's DDL). DDL now waits at most lock_timeout per attempt and retries the whole transaction."""
        digest = hashlib.sha256("\0".join([install or "", *texts]).encode()).hexdigest()
        for attempt in range(attempts):
            try:
                with self.transaction() as conn:
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
                    first = False
                    if install is not None:
                        exists = conn.execute("SELECT to_regclass('public.schema_version') AS name").fetchone()
                        if exists["name"]:
                            versions = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
                            if versions != [{"version": 1}]:
                                raise RuntimeError("Unrecognized database schema version; explicit migration required")
                        else:
                            first = True
                    if not first and not force and self._applied(conn, name) == digest:
                        return
                    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
                    if first:
                        conn.execute(install)
                    for text in texts:
                        conn.execute(text)
                    conn.execute("""CREATE TABLE IF NOT EXISTS schema_digests(
                        name text PRIMARY KEY, digest text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
                    conn.execute("""INSERT INTO schema_digests(name,digest) VALUES (%s,%s)
                        ON CONFLICT (name) DO UPDATE SET digest=excluded.digest, applied_at=now()""", (name, digest))
                return
            except RETRYABLE:
                if attempt == attempts - 1:
                    raise
                sleep(min(2 * (attempt + 1), 10))

    @staticmethod
    def _applied(conn, name):
        if not conn.execute("SELECT to_regclass('public.schema_digests') AS name").fetchone()["name"]:
            return None
        row = conn.execute("SELECT digest FROM schema_digests WHERE name=%s", (name,)).fetchone()
        return row["digest"] if row else None

    def health(self) -> bool:
        with self.transaction() as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
