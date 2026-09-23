CREATE TABLE IF NOT EXISTS model_accounts (
 profile text PRIMARY KEY CHECK(profile IN ('primary','backup')),
 authentication text NOT NULL DEFAULT 'unknown', checked_at timestamptz,
 paused_until timestamptz, last_fault text, last_success_at timestamptz,
 notified_revision bigint
);
INSERT INTO model_accounts(profile) VALUES ('primary'),('backup') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS model_account_policy (
 id integer PRIMARY KEY CHECK(id=1), profile text NOT NULL REFERENCES model_accounts(profile),
 revision bigint NOT NULL DEFAULT 0, control_project_id uuid REFERENCES projects(id)
);
INSERT INTO model_account_policy(id,profile) VALUES(1,'primary') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS model_account_commands (
 id uuid PRIMARY KEY REFERENCES tasks(id), sequence bigserial UNIQUE,
 event_key text NOT NULL UNIQUE, project_id uuid NOT NULL REFERENCES projects(id),
 owner_user text NOT NULL, action text NOT NULL CHECK(action IN ('status','switch')),
 target text REFERENCES model_accounts(profile), state text NOT NULL DEFAULT 'requested',
 receipt jsonb, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS model_account_calls (
 request_id text PRIMARY KEY, input_digest text NOT NULL,
 profile text NOT NULL REFERENCES model_accounts(profile), revision bigint NOT NULL,
 state text NOT NULL DEFAULT 'dispatching', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS model_account_wakes (
 command_id uuid NOT NULL REFERENCES model_account_commands(id),
 turn_id uuid NOT NULL REFERENCES turns(id), revision bigint NOT NULL,
 done boolean NOT NULL DEFAULT false, PRIMARY KEY(command_id,turn_id)
);
