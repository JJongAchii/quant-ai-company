CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY);
CREATE TABLE projects (
 id uuid PRIMARY KEY, title text NOT NULL, instruction text NOT NULL,
 owner_user text NOT NULL, channel text, thread_ts text,
 revision integer NOT NULL DEFAULT 1, status text NOT NULL DEFAULT 'active',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tasks (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 parent_id uuid REFERENCES tasks(id), agent text NOT NULL, instruction text NOT NULL,
 revision integer NOT NULL, depth integer NOT NULL DEFAULT 0, priority integer NOT NULL DEFAULT 0,
 status text NOT NULL DEFAULT 'pending', turn_count integer NOT NULL DEFAULT 0,
 result text, error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX project_slack_thread ON projects(channel,thread_ts) WHERE channel IS NOT NULL;
CREATE INDEX task_parent ON tasks(parent_id, status);
CREATE TABLE turns (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id), sequence integer NOT NULL,
 revision integer NOT NULL, status text NOT NULL DEFAULT 'queued',
 due_at timestamptz NOT NULL DEFAULT now(), workflow_started boolean NOT NULL DEFAULT false,
 request jsonb, response jsonb, error text, attempts integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(task_id, sequence)
);
CREATE INDEX turn_dispatch ON turns(workflow_started, status);
CREATE TABLE messages (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 task_id uuid REFERENCES tasks(id), revision integer NOT NULL, author text NOT NULL,
 recipient text, kind text NOT NULL, text text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX message_context ON messages(project_id, created_at);
CREATE TABLE inbound (
 event_key text PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 task_id uuid NOT NULL REFERENCES tasks(id), payload_digest text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox (
 id uuid PRIMARY KEY REFERENCES messages(id), project_id uuid NOT NULL REFERENCES projects(id),
 revision integer NOT NULL, agent text NOT NULL, channel text NOT NULL, thread_ts text NOT NULL,
 text text NOT NULL, status text NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
 next_at timestamptz NOT NULL DEFAULT now(), sent_ts text, error text,
 started_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbox_dispatch ON outbox(status, next_at);
CREATE TABLE artifacts (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 task_id uuid NOT NULL REFERENCES tasks(id), revision integer NOT NULL,
 title text NOT NULL, content text NOT NULL, source_ids jsonb NOT NULL,
 digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sources (
 id text PRIMARY KEY, title text NOT NULL, uri text NOT NULL, content text NOT NULL,
 available_at timestamptz NOT NULL, approved boolean NOT NULL DEFAULT false,
 project_id uuid REFERENCES projects(id), synthetic boolean NOT NULL DEFAULT false
);
CREATE TABLE memories (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 agent text NOT NULL, text text NOT NULL, source_ids jsonb NOT NULL,
 status text NOT NULL DEFAULT 'proposed', shared boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE daily_usage (day date PRIMARY KEY, reserved integer NOT NULL DEFAULT 0);
CREATE TABLE runtime_control (
 id integer PRIMARY KEY CHECK(id=1), paused_until timestamptz, reason text
);
INSERT INTO runtime_control(id) VALUES (1);
CREATE TABLE events (
 id bigserial PRIMARY KEY, project_id uuid REFERENCES projects(id),
 kind text NOT NULL, detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_version(version) VALUES (1);
