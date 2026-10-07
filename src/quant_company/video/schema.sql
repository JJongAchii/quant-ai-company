CREATE TABLE IF NOT EXISTS video_jobs (
 id uuid PRIMARY KEY, edition_id uuid NOT NULL REFERENCES brief_editions(id),
 version integer NOT NULL CHECK(version>0), source_digest text NOT NULL, source jsonb NOT NULL,
 policy jsonb NOT NULL, state text NOT NULL DEFAULT 'queued',
 plan jsonb, review jsonb, artifacts jsonb, artifact_digest text,
 youtube_id text, upload_session text, review_message_id uuid REFERENCES messages(id),
 feedback text NOT NULL DEFAULT '', error text, lease_until timestamptz,
 publish_deadline timestamptz NOT NULL, approved_at timestamptz, approved_by text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(edition_id,version)
);
CREATE TABLE IF NOT EXISTS video_effects (
 id text PRIMARY KEY, job_id uuid NOT NULL REFERENCES video_jobs(id),
 request_digest text NOT NULL, kind text NOT NULL, state text NOT NULL,
 credits integer NOT NULL DEFAULT 0 CHECK(credits>=0), receipt jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS video_actions (
 event_key text PRIMARY KEY, job_id uuid NOT NULL REFERENCES video_jobs(id),
 owner_user text NOT NULL, action text NOT NULL, artifact_digest text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS video_jobs_pending ON video_jobs(state,created_at);
CREATE TABLE IF NOT EXISTS video_reconciliations (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 job_id uuid NOT NULL REFERENCES video_jobs(id), effect_key text,
 note text NOT NULL, receipt jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
