CREATE TABLE IF NOT EXISTS research_workers (
 id text PRIMARY KEY CHECK (id='worker'), last_seen timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_jobs (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 task_id uuid NOT NULL REFERENCES tasks(id), revision integer NOT NULL,
 recipe_id text NOT NULL, manifest jsonb NOT NULL, manifest_digest text NOT NULL,
 company_commit text NOT NULL,
 state text NOT NULL DEFAULT 'pending_approval' CHECK (state IN
 ('pending_approval','queued','claimed','running','cancel_requested','uncertain','received',
  'awaiting_audit','completed','cancelled','failed')),
 approval_event_id text UNIQUE, approved_by text, approved_at timestamptz,
 worker_id text, lease_token text, claimed_at timestamptz, heartbeat_at timestamptz,
 sequence integer NOT NULL DEFAULT 0, update_digest text,
 artifact_sha256 text, artifact_path text, report jsonb, error text,
 notified_state text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project_id,revision,recipe_id,manifest_digest),
 CHECK ((worker_id IS NULL) = (lease_token IS NULL)),
 CHECK (worker_id IS NULL OR worker_id='worker'),
 CHECK (approval_event_id IS NULL OR approved_by IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS research_worker_exclusive ON research_jobs(worker_id)
 WHERE state IN ('claimed','running','cancel_requested','uncertain');
CREATE INDEX IF NOT EXISTS research_project ON research_jobs(project_id,created_at);
