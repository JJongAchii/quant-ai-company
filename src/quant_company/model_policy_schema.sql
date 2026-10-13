CREATE TABLE IF NOT EXISTS model_assignment_policy (
 id integer PRIMARY KEY CHECK(id=1), revision bigint NOT NULL DEFAULT 0,
 bindings jsonb NOT NULL DEFAULT '{}'::jsonb
);
INSERT INTO model_assignment_policy(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS model_assignment_revisions (
 revision bigint PRIMARY KEY, bindings jsonb NOT NULL, command_id uuid REFERENCES tasks(id),
 owner_user text, created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO model_assignment_revisions(revision,bindings) VALUES(0,'{}') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS model_assignment_commands (
 id uuid PRIMARY KEY REFERENCES tasks(id), sequence bigserial UNIQUE,
 event_key text NOT NULL UNIQUE, owner_user text NOT NULL, command jsonb NOT NULL,
 state text NOT NULL DEFAULT 'requested', receipt jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS model_catalog_checks (
 id uuid PRIMARY KEY REFERENCES model_assignment_commands(id), profile text NOT NULL,
 account_revision bigint NOT NULL, models jsonb NOT NULL, checked_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS model_selection jsonb;
CREATE TABLE IF NOT EXISTS model_execution_bindings (
 request_id text PRIMARY KEY, target text NOT NULL, selection jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
