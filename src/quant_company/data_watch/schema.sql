CREATE TABLE IF NOT EXISTS data_watch_inventory (
 id text PRIMARY KEY, policy text NOT NULL, state text NOT NULL DEFAULT 'running',
 lease_token uuid NOT NULL, lease_until timestamptz NOT NULL, receipt jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), checked_at timestamptz
);
CREATE TABLE IF NOT EXISTS data_watch_datasets (
 id text PRIMARY KEY, lake text NOT NULL, dataset text NOT NULL, source jsonb NOT NULL,
 version text NOT NULL, present boolean NOT NULL DEFAULT true, observed_at timestamptz NOT NULL,
 descriptor jsonb, descriptor_version text, error text, described_at timestamptz,
 last_healthy_at timestamptz, next_describe_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, lease_until timestamptz, UNIQUE(lake,dataset)
);
CREATE TABLE IF NOT EXISTS data_watch_checks (
 id uuid PRIMARY KEY, research_job_id uuid NOT NULL REFERENCES research_jobs(id), revision integer NOT NULL,
 scope jsonb NOT NULL, scope_digest text NOT NULL, policy text NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','complete','stale')),
 lease_token text, lease_until timestamptz, receipt jsonb, receipt_digest text,
 created_at timestamptz NOT NULL DEFAULT now(), checked_at timestamptz
);
CREATE TABLE IF NOT EXISTS data_watch_descriptions (
 id uuid PRIMARY KEY, dataset_id text NOT NULL REFERENCES data_watch_datasets(id),
 version text NOT NULL, receipt jsonb NOT NULL, accepted boolean NOT NULL, checked_at timestamptz NOT NULL
);
ALTER TABLE data_watch_datasets ADD COLUMN IF NOT EXISTS descriptor_receipt_id uuid;
CREATE TABLE IF NOT EXISTS data_watch_incidents (
 id uuid PRIMARY KEY, issue_key text NOT NULL UNIQUE, policy text NOT NULL,
 project_id uuid NOT NULL UNIQUE REFERENCES projects(id), root_message_id uuid NOT NULL,
 state text NOT NULL, detail jsonb NOT NULL, transition integer NOT NULL DEFAULT 1,
 announced_transition integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
 checked_at timestamptz NOT NULL DEFAULT now(), last_healthy_at timestamptz
);
CREATE TABLE IF NOT EXISTS data_watch_publications (
 id uuid PRIMARY KEY REFERENCES outbox(id), policy text NOT NULL,
 incident_id uuid REFERENCES data_watch_incidents(id),
 kind text NOT NULL CHECK(kind IN ('root','transition','summary')), expires_at timestamptz NOT NULL
);
ALTER TABLE data_watch_publications ADD COLUMN IF NOT EXISTS check_ids jsonb NOT NULL DEFAULT '[]';
