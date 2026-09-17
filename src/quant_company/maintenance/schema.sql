-- Additive, independently versioned tables. No existing company records are changed.
CREATE TABLE IF NOT EXISTS maintenance_version (version integer PRIMARY KEY CHECK (version=1));
INSERT INTO maintenance_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS maintenance_jobs (
 id uuid PRIMARY KEY, kind text NOT NULL, state text NOT NULL,
 problem_key text UNIQUE, payload jsonb NOT NULL, receipt jsonb NOT NULL DEFAULT '{}',
 error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS maintenance_job_queue ON maintenance_jobs(state, created_at);
CREATE TABLE IF NOT EXISTS maintenance_observations (
 key text PRIMARY KEY, job_id uuid NOT NULL REFERENCES maintenance_jobs(id),
 body jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS maintenance_calls (
 id text PRIMARY KEY, job_id uuid NOT NULL REFERENCES maintenance_jobs(id),
 request jsonb NOT NULL, response jsonb, error text,
 due_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS maintenance_control (
 id integer PRIMARY KEY CHECK (id=1), next_observe_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO maintenance_control(id) VALUES (1) ON CONFLICT DO NOTHING;
-- Bounded look-back diagnostics over the existing company records.
CREATE INDEX IF NOT EXISTS maintenance_turn_history ON turns(created_at);
CREATE INDEX IF NOT EXISTS maintenance_task_history ON tasks(created_at);

-- Human approval is bound to one immutable candidate, independently of model turns.
CREATE TABLE IF NOT EXISTS maintenance_applications (
 id uuid PRIMARY KEY, job_id uuid NOT NULL UNIQUE REFERENCES maintenance_jobs(id),
 project_id uuid NOT NULL REFERENCES projects(id), owner_user text NOT NULL,
 event_key text NOT NULL UNIQUE, approval_text text NOT NULL, head text NOT NULL,
 state text NOT NULL DEFAULT 'approved', receipt jsonb NOT NULL DEFAULT '{}', error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
