-- Additive migration, safe with existing v1 records and rollback to the previous service.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS clarification text;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'work';
CREATE INDEX IF NOT EXISTS task_routing_queue ON tasks(project_id,created_at,id) WHERE kind='routing';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS finance_fetches (
 turn_id uuid PRIMARY KEY REFERENCES turns(id), request_digest text NOT NULL,
 result jsonb NOT NULL, original_html text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS company_policy (
 id integer PRIMARY KEY CHECK(id=1), revision integer NOT NULL DEFAULT 0,
 remove_company_daily_limit boolean NOT NULL DEFAULT false,
 remove_maintenance_daily_limit boolean NOT NULL DEFAULT false
);
INSERT INTO company_policy(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS policy_commands (
 event_key text PRIMARY KEY, owner_user text NOT NULL, project_id uuid NOT NULL REFERENCES projects(id),
 request_text text NOT NULL, receipt jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS repository_evidence (
 commit text PRIMARY KEY, snapshot jsonb NOT NULL, files jsonb NOT NULL,
 metadata jsonb NOT NULL, checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS system_verifications (
 id text PRIMARY KEY, feature text NOT NULL, code_commit text NOT NULL,
 config_digest text, scope text NOT NULL, evidence jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS finding_assessments (
 id text PRIMARY KEY, case_id uuid NOT NULL, disposition text NOT NULL
 CHECK(disposition IN ('unverified','reproduced','resolved','invalidated')),
 reason text NOT NULL, evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS finding_assessment_case ON finding_assessments(case_id,created_at);
CREATE TABLE IF NOT EXISTS maintenance_revisions (
 job_id uuid NOT NULL, revision integer NOT NULL, payload jsonb NOT NULL, receipt jsonb NOT NULL,
 reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(job_id,revision)
);
