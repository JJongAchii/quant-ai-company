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
-- v3: findings never stop production. Attempts, fixes and remaining findings travel with the job to the owner.
ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS qa jsonb NOT NULL DEFAULT '{}'::jsonb;
-- v4: performance analytics of published episodes (VIDEO_ANALYTICS_ENABLED). Uploads are the owner's own (Slack file
-- delivery), so each public upload is linked to its job by the title suffix; reads only, except the reporting jobs.
CREATE TABLE IF NOT EXISTS video_publications (
 youtube_id text PRIMARY KEY CHECK(youtube_id ~ '^[A-Za-z0-9_-]{11}$'),
 job_id uuid REFERENCES video_jobs(id), edition_id uuid REFERENCES brief_editions(id),
 edition_kind text CHECK(edition_kind IN ('am','close')), title text NOT NULL, proposed_title text,
 published_at timestamptz NOT NULL, duration_s integer NOT NULL CHECK(duration_s>=0), thumbnail_url text,
 matched_by text CHECK(matched_by IN ('title_suffix','exact_title','manual')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS video_publications_recent ON video_publications(published_at);
-- One YouTube Reporting API job per report type; created once, its receipt kept.
CREATE TABLE IF NOT EXISTS video_report_jobs (
 report_type text PRIMARY KEY, job_id text NOT NULL, receipt jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
-- Each downloaded daily report; a newer report for the same type and day replaces that day's rows, never adds to them.
CREATE TABLE IF NOT EXISTS video_report_downloads (
 report_id text PRIMARY KEY, report_type text NOT NULL, day date NOT NULL, report_created timestamptz NOT NULL,
 sha256 text NOT NULL, row_count integer NOT NULL, applied boolean NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS video_reach_daily (
 youtube_id text NOT NULL, day date NOT NULL, source text NOT NULL, impressions integer NOT NULL CHECK(impressions>=0),
 clicks numeric NOT NULL CHECK(clicks>=0), report_id text NOT NULL REFERENCES video_report_downloads(report_id),
 PRIMARY KEY(youtube_id,day,source)
);
CREATE TABLE IF NOT EXISTS video_snapshots (
 youtube_id text NOT NULL REFERENCES video_publications(youtube_id), horizon text NOT NULL CHECK(horizon IN ('d3','d7','d28')),
 window_start date NOT NULL, window_end date NOT NULL, metrics jsonb NOT NULL,
 taken_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(youtube_id,horizon)
);
-- One model request per analysis (stable request ID); the receipt follows the video_effects model rules. The
-- request (prompt, thumbnail file digests, effort) is frozen on the first ask and re-sent unchanged.
CREATE TABLE IF NOT EXISTS video_analyses (
 id uuid PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('video','weekly')), request_id text NOT NULL UNIQUE,
 youtube_id text REFERENCES video_publications(youtube_id), week date, request_digest text NOT NULL,
 request jsonb, state text NOT NULL CHECK(state IN ('running','completed','failed','rejected')), receipt jsonb, output jsonb,
 message_id uuid REFERENCES messages(id), created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
-- Proposed in the weekly review; only the owner's Slack decision makes a lesson active. Active lessons are frozen into
-- each new video job's policy, so a job's prompt never changes after it was created.
CREATE TABLE IF NOT EXISTS video_lessons (
 id uuid PRIMARY KEY, analysis_id uuid NOT NULL REFERENCES video_analyses(id), text text NOT NULL,
 scope text NOT NULL CHECK(scope IN ('title','thumbnail','opening','structure','length','keywords')),
 evidence jsonb NOT NULL, state text NOT NULL DEFAULT 'proposed' CHECK(state IN ('proposed','active','rejected','retired')),
 decided_by text, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS video_lesson_actions (
 event_key text PRIMARY KEY, lesson_id uuid NOT NULL REFERENCES video_lessons(id), owner_user text NOT NULL,
 action text NOT NULL CHECK(action IN ('adopt','reject','retire')), outcome text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
-- v5: the owner's replies in analytics threads (a card or a weekly review), each answered once by claude-runtime in
-- the same thread. An answer is an analysis of kind 'answer'; an instruction for future episodes comes back as a
-- proposed lesson on that answer.
ALTER TABLE video_analyses DROP CONSTRAINT IF EXISTS video_analyses_kind_check;
ALTER TABLE video_analyses ADD CONSTRAINT video_analyses_kind_check CHECK(kind IN ('video','weekly','answer'));
CREATE TABLE IF NOT EXISTS video_questions (
 id uuid PRIMARY KEY, event_key text NOT NULL UNIQUE, thread_key text NOT NULL,
 youtube_id text REFERENCES video_publications(youtube_id), week date, root_message_id uuid NOT NULL REFERENCES messages(id),
 owner_user text NOT NULL, channel text NOT NULL, thread_ts text NOT NULL, message_ts text NOT NULL,
 text text NOT NULL CHECK(length(text) BETWEEN 1 AND 2000),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','answered','failed','expired')),
 workflow_started boolean NOT NULL DEFAULT false, waiting_noticed boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz
);
CREATE INDEX IF NOT EXISTS video_questions_open ON video_questions(created_at) WHERE state='pending';
