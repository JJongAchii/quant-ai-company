-- Additive migration, safe with existing v1 records and rollback to the previous service.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS clarification text;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'work';
CREATE INDEX IF NOT EXISTS task_routing_queue ON tasks(project_id,created_at,id) WHERE kind='routing';
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
-- Search and original-page reads survive worker retries without inventing a new model request.
ALTER TABLE sources ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS web_requests (
    id text PRIMARY KEY,
    turn_id uuid REFERENCES turns(id),
    maintenance_job_id uuid,
    operation text NOT NULL CHECK (operation IN ('web_search','web_read')),
    arguments jsonb NOT NULL,
    provider_request jsonb,
    receipt jsonb,
    original bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK ((turn_id IS NULL) <> (maintenance_job_id IS NULL))
);
CREATE INDEX IF NOT EXISTS web_requests_turn ON web_requests(turn_id);
-- Opaque controls are issued only with a server-created research approval notice.
CREATE TABLE IF NOT EXISTS research_approval_bindings (
 id uuid PRIMARY KEY, message_id uuid NOT NULL UNIQUE REFERENCES outbox(id),
 project_id uuid NOT NULL REFERENCES projects(id), target_kind text NOT NULL, target_id uuid NOT NULL,
 revision integer NOT NULL, manifest_digest text NOT NULL, owner_user text NOT NULL,
 team_id text NOT NULL, app_id text, channel text NOT NULL, thread_ts text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS research_approval_project ON research_approval_bindings(project_id,revision);
-- Server-owned updates target only a previously receipted bot message.
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS update_ts text;
