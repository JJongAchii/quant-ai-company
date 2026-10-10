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
-- v2: owner file delivery in the brief's Slack thread (VIDEO_UPLOAD_ENABLED=false). One row per delivery attempt;
-- receipts record each Slack file ID before its bytes are sent. A lost completion stays uncertain, never replayed.
ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS delivery_message_id uuid REFERENCES messages(id);
CREATE TABLE IF NOT EXISTS video_deliveries (
 id uuid PRIMARY KEY REFERENCES messages(id), job_id uuid NOT NULL REFERENCES video_jobs(id),
 attempt integer NOT NULL CHECK(attempt>0), state text NOT NULL DEFAULT 'pending',
 receipt jsonb NOT NULL DEFAULT '{}'::jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(job_id,attempt)
);
-- Editions delivered without a substantive body (fallback notice): no video, one thread alert.
CREATE TABLE IF NOT EXISTS video_skips (
 edition_id uuid PRIMARY KEY REFERENCES brief_editions(id), reason text NOT NULL,
 alert_message_id uuid REFERENCES messages(id), created_at timestamptz NOT NULL DEFAULT now()
);
-- Reviewed-library images shown per episode; images used in the last 7 days are not offered again.
CREATE TABLE IF NOT EXISTS video_asset_usage (
 job_id uuid NOT NULL REFERENCES video_jobs(id), asset_id text NOT NULL, file text NOT NULL,
 slot text NOT NULL CHECK(slot IN ('thumbnail','scene')), used_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(job_id,asset_id,slot)
);
CREATE INDEX IF NOT EXISTS video_asset_usage_recent ON video_asset_usage(used_at);
