"""PostgreSQL owns observation leases and receipts; all network reads happen outside transactions."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint
from .contracts import CatalogItem, ObjectVersion, freshness, load_contracts

LOCK = 71350264


def utcnow():
    return datetime.now(UTC)


class DataWatchStore:
    def __init__(self, company):
        self.company, self.db = company, company.db

    def authorized(self):
        s = self.company.settings
        role = self.company.roles.get("data")
        return (s.data_watch_enabled and s.data_watch_owner_user in s.slack_allowed_users
                and s.data_watch_channel_id in s.slack_allowed_channels
                and s.data_watch_channel_id.startswith("C") and role is not None and role.active
                and s.data_watch_channel_id != s.improvements_channel_id)

    def policy(self):
        s = self.company.settings
        return fingerprint({"v": 1, "enabled": s.data_watch_enabled, "publish": s.data_watch_publish_enabled,
                            "core": s.data_watch_core_enabled, "lake": s.company_lake_uri,
                            "owner": s.data_watch_owner_user, "channel": s.data_watch_channel_id,
                            "team": s.slack_team_id,
                            "contracts": {k: v.model_dump(mode="json") for k, v in self.contracts().items()}})

    def contracts(self):
        return load_contracts(self.company.settings.data_watch_contracts_file)

    def claim_inventory(self):
        if not self.authorized():
            return None
        at, policy = utcnow(), self.policy()
        identity = fingerprint([policy, int(at.timestamp()) // 1800])
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            old = conn.execute("SELECT * FROM data_watch_inventory WHERE id=%s", (identity,)).fetchone()
            if old and (old["state"] == "complete" or old["lease_until"] > at):
                return None
            return conn.execute("""INSERT INTO data_watch_inventory(id,policy,lease_token,lease_until)
                VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET
                lease_token=excluded.lease_token,lease_until=excluded.lease_until RETURNING *""",
                (identity, policy, uuid4(), at + timedelta(minutes=2))).fetchone()

    def save_inventory(self, claim, receipt):
        at = utcnow()
        items = []
        if receipt.get("ok"):
            try:
                entries = receipt["data"]["datasets"]
                if not isinstance(entries, list) or len(entries) > 200:
                    raise ValueError("unbounded_catalog")
                items = [CatalogItem.model_validate(item) for item in entries]
                if len({item.dataset for item in items}) != len(items):
                    raise ValueError("duplicate_catalog_entry")
                prefix = self.company.settings.company_lake_uri.rstrip("/") + "/"
                if any(not item.uri.startswith(prefix) for item in items):
                    raise ValueError("catalog_outside_lake")
            except (KeyError, TypeError, ValueError):
                receipt = {"ok": False, "error": "invalid_catalog_receipt"}
        else:
            receipt = {"ok": False, "error": "lake_catalog_unavailable"}
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            old = conn.execute("SELECT * FROM data_watch_inventory WHERE id=%s FOR UPDATE", (claim["id"],)).fetchone()
            if (not self.authorized() or self.policy() != claim["policy"] or not old
                    or old["lease_token"] != claim["lease_token"] or old["state"] == "complete"):
                return False
            conn.execute("""UPDATE data_watch_inventory SET state='complete',receipt=%s,checked_at=%s
                WHERE id=%s""", (Jsonb(as_json(receipt)), at, claim["id"]))
            if not receipt.get("ok"):
                return True  # A failed catalog is never evidence that datasets were removed.
            lake = self.company.settings.company_lake_uri
            conn.execute("UPDATE data_watch_datasets SET present=false WHERE lake=%s AND NOT(dataset=ANY(%s))",
                         (lake, [item.dataset for item in items]))
            for item in items:
                source = ObjectVersion.model_validate(item.model_dump(exclude={"dataset"})).model_dump(mode="json")
                conn.execute("""INSERT INTO data_watch_datasets
                    (id,lake,dataset,source,version,observed_at,next_describe_at) VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(lake,dataset) DO UPDATE SET source=excluded.source,version=excluded.version,
                    present=true,observed_at=excluded.observed_at,
                    next_describe_at=CASE WHEN data_watch_datasets.version<>excluded.version OR NOT data_watch_datasets.present
                        THEN excluded.next_describe_at ELSE data_watch_datasets.next_describe_at END,
                    descriptor_version=CASE WHEN data_watch_datasets.present THEN data_watch_datasets.descriptor_version END
                    """, (fingerprint([lake, item.dataset]), lake, item.dataset, Jsonb(source), fingerprint(source), at, at))
            return True

    def claim_descriptions(self):
        if not self.authorized():
            return []
        at = utcnow()
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT * FROM data_watch_datasets WHERE lake=%s AND present
                AND (descriptor_version IS DISTINCT FROM version OR error IS NOT NULL)
                AND next_describe_at<=%s AND (lease_until IS NULL OR lease_until<=%s)
                ORDER BY next_describe_at,dataset FOR UPDATE SKIP LOCKED LIMIT 4""",
                (self.company.settings.company_lake_uri, at, at)).fetchall()
            for row in rows:
                row["lease_token"], row["policy"] = uuid4(), self.policy()
                conn.execute("UPDATE data_watch_datasets SET lease_token=%s,lease_until=%s WHERE id=%s",
                             (row["lease_token"], at + timedelta(minutes=2), row["id"]))
            return rows

    def save_description(self, claim, receipt):
        at, error = utcnow(), None
        try:
            data = receipt["data"]
            version = ObjectVersion.model_validate(data["source"]).model_dump(mode="json")
            if (receipt.get("ok") is not True or data["dataset"] != claim["dataset"]
                    or fingerprint(version) != claim["version"] or data.get("sample") is not None
                    or data.get("method") != "parquet_footer_statistics"
                    or type(data.get("row_count")) is not int or data["row_count"] < 0
                    or len(str(receipt).encode()) > 32768):
                raise ValueError("descriptor_identity_or_scope")
        except (ValueError, KeyError, TypeError):
            # qdata's public bounded-inspection contract can reject an otherwise
            # readable object. Keep a safe reason code; never persist arbitrary
            # provider exception text from the short-lived reader.
            footer_limit = (isinstance(receipt, dict)
                            and receipt.get("error") == "inspection_limit_or_invalid_request"
                            and receipt.get("detail") == "Parquet footer exceeds the 2 MiB inspection budget")
            error = "parquet_footer_limit" if footer_limit else "descriptor_unavailable_or_changed"
            receipt = {"ok": False, "error": error}
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM data_watch_datasets WHERE id=%s FOR UPDATE", (claim["id"],)).fetchone()
            accepted = (self.authorized() and self.policy() == claim["policy"] and row["version"] == claim["version"]
                        and row["lease_token"] == claim["lease_token"] and row["present"])
            conn.execute("""INSERT INTO data_watch_descriptions(id,dataset_id,version,receipt,accepted,checked_at)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (claim["lease_token"], claim["id"],
                claim["version"], Jsonb(as_json(receipt)), accepted, at))
            if not accepted:
                return False
            conn.execute("""UPDATE data_watch_datasets SET descriptor=%s,descriptor_version=%s,error=%s,
                described_at=%s,next_describe_at=%s,lease_token=NULL,lease_until=NULL,descriptor_receipt_id=%s WHERE id=%s""",
                (Jsonb(as_json(receipt)), claim["version"], error, at, at + timedelta(minutes=30), claim["lease_token"], claim["id"]))
            return True

    def snapshot(self, conn):
        at, contracts = utcnow(), self.contracts()
        inventory = conn.execute("""SELECT id,receipt,checked_at FROM data_watch_inventory
            WHERE policy=%s AND state='complete' ORDER BY checked_at DESC LIMIT 1""", (self.policy(),)).fetchone()
        datasets = []
        for row in conn.execute("SELECT * FROM data_watch_datasets WHERE lake=%s ORDER BY dataset LIMIT 200",
                                (self.company.settings.company_lake_uri,)).fetchall():
            current = row["descriptor_version"] == row["version"] and not row["error"] and row["present"]
            descriptor = row["descriptor"] if current else None
            timing = freshness(contracts.get(row["dataset"]), descriptor, at)
            problem = ("dataset_missing" if not row["present"] else row["error"]
                       if row["descriptor_version"] == row["version"] and row["error"] else
                       "empty_dataset" if current and descriptor["data"]["row_count"] == 0 else
                       "data_late" if timing["state"] == "stale" else None)
            datasets.append({"id": row["id"], "dataset": row["dataset"], "source": row["source"],
                             "version": row["version"], "observed_at": row["observed_at"],
                             "descriptor_receipt_id": row["descriptor_receipt_id"],
                             "described_at": row["described_at"], "last_healthy_at": row["last_healthy_at"],
                             "inspection": "metadata_checked" if current else "unchecked", "freshness": timing,
                             "date_bounds": descriptor["data"].get("date_bounds", {}) if current else {},
                             "problem": problem})
        from .core import CoreChecks

        return as_json({"checked_at": at, "inventory": inventory, "datasets": datasets,
                        "core_checks": CoreChecks(self).status(conn), "next_inventory_at":
                        datetime.fromtimestamp((int(at.timestamp()) // 1800 + 1) * 1800, UTC),
                        "scope": "Catalog and parquet footers; per-instrument coverage is unchecked. "
                                 "Latest lake status does not change frozen research inputs."})

    def status(self):
        with self.db.transaction() as conn:
            return self.snapshot(conn)

    def report(self):
        from .reporting import refresh

        if not self.authorized():
            return {"state": "disabled"}
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            return refresh(conn, self, self.snapshot(conn))
