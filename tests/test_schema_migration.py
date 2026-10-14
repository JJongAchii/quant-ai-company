import threading
import time
from importlib.resources import files

import psycopg
import pytest

from quant_company.db import CORE_SCHEMA


def core_texts():
    return [files(package).joinpath(name).read_text() for package, name in CORE_SCHEMA]


def install_text():
    return files("quant_company").joinpath("schema.sql").read_text()


def index_exists(company, name):
    with company.db.transaction() as conn:
        return conn.execute("SELECT to_regclass(%s) AS name", ("public." + name,)).fetchone()["name"] is not None


def hold_table(url, table, seconds, started):
    with psycopg.connect(url) as conn:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # AccessShareLock until the transaction ends
        started.set()
        time.sleep(seconds)


def test_unchanged_schema_never_touches_busy_tables(company):
    with company.db.transaction() as conn:
        assert conn.execute("SELECT digest FROM schema_digests WHERE name='core'").fetchone()["digest"]
    started = threading.Event()
    holder = threading.Thread(target=hold_table, args=(company.db.url, "tasks", 4, started))
    holder.start()
    started.wait(5)
    began = time.monotonic()
    company.db.migrate()  # A rerun of ALTER TABLE tasks ... IF NOT EXISTS would wait behind the open reader.
    assert time.monotonic() - began < 2
    holder.join()


def test_changed_schema_is_applied_and_force_reruns_it(company):
    with company.db.transaction() as conn:
        conn.execute("DROP INDEX task_routing_queue")
    company.db.migrate()
    assert not index_exists(company, "task_routing_queue")  # same text: nothing reruns
    with company.db.transaction() as conn:
        conn.execute("UPDATE schema_digests SET digest='stale' WHERE name='core'")
    company.db.migrate()
    assert index_exists(company, "task_routing_queue")
    with company.db.transaction() as conn:
        conn.execute("DROP INDEX task_routing_queue")
    company.db.migrate(force=True)
    assert index_exists(company, "task_routing_queue")


def test_busy_table_retries_the_whole_migration_until_the_lock_clears(company):
    with company.db.transaction() as conn:
        conn.execute("UPDATE schema_digests SET digest='stale' WHERE name='core'")
    started, waits = threading.Event(), []
    holder = threading.Thread(target=hold_table, args=(company.db.url, "tasks", 1.0, started))
    holder.start()
    started.wait(5)
    company.db.apply_schema("core", core_texts(), lock=71350219, install=install_text(), lock_timeout="200ms",
                            attempts=20, sleep=lambda seconds: (waits.append(seconds), time.sleep(0.2)))
    holder.join()
    assert waits
    with company.db.transaction() as conn:
        assert conn.execute("SELECT digest FROM schema_digests WHERE name='core'").fetchone()["digest"] != "stale"


def test_migration_gives_up_while_the_table_stays_busy(company):
    with company.db.transaction() as conn:
        conn.execute("UPDATE schema_digests SET digest='stale' WHERE name='core'")
    started = threading.Event()
    holder = threading.Thread(target=hold_table, args=(company.db.url, "tasks", 3, started))
    holder.start()
    started.wait(5)
    with pytest.raises(psycopg.errors.LockNotAvailable):
        company.db.apply_schema("core", core_texts(), lock=71350219, install=install_text(), lock_timeout="100ms",
                                attempts=2, sleep=lambda seconds: None)
    holder.join()
    with company.db.transaction() as conn:
        assert conn.execute("SELECT digest FROM schema_digests WHERE name='core'").fetchone()["digest"] == "stale"


def test_maintenance_schema_has_its_own_digest(company):
    text = files("quant_company.maintenance").joinpath("schema.sql").read_text()
    company.db.apply_schema("maintenance", [text], lock=71350220)
    started = threading.Event()
    holder = threading.Thread(target=hold_table, args=(company.db.url, "turns", 4, started))
    holder.start()
    started.wait(5)
    began = time.monotonic()
    company.db.apply_schema("maintenance", [text], lock=71350220)  # CREATE INDEX ON turns would wait
    assert time.monotonic() - began < 2
    holder.join()
    with company.db.transaction() as conn:
        names = {row["name"] for row in conn.execute("SELECT name FROM schema_digests").fetchall()}
    assert names == {"core", "maintenance"}
