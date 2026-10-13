CREATE TABLE IF NOT EXISTS trend_feed_source (
 id integer PRIMARY KEY CHECK(id=1), next_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz, last_success timestamptz, last_attempt timestamptz,
 etag text, modified text, failures integer NOT NULL DEFAULT 0, error text,
 lease_token uuid, lease_until timestamptz
);
INSERT INTO trend_feed_source(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS trend_feed_snapshots (
 id uuid PRIMARY KEY, observed_at timestamptz NOT NULL, ok boolean NOT NULL, receipt jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS trend_snapshots_time ON trend_feed_snapshots(observed_at);
CREATE TABLE IF NOT EXISTS trend_feed_keywords (
 keyword text PRIMARY KEY, first_seen timestamptz NOT NULL, last_seen timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS trend_feed_observations (
 snapshot_id uuid NOT NULL REFERENCES trend_feed_snapshots(id) ON DELETE CASCADE,
 keyword text NOT NULL REFERENCES trend_feed_keywords(keyword), observed_at timestamptz NOT NULL,
 item jsonb NOT NULL, PRIMARY KEY(snapshot_id,keyword)
);
CREATE INDEX IF NOT EXISTS trend_observations_time ON trend_feed_observations(observed_at);
CREATE TABLE IF NOT EXISTS trend_feed_digests (
 id uuid PRIMARY KEY, day date NOT NULL, geo text NOT NULL DEFAULT 'KR', channel text NOT NULL,
 owner_user text NOT NULL, cutoff timestamptz NOT NULL, send_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL, policy_digest text NOT NULL, state text NOT NULL DEFAULT 'preparing',
 bundle jsonb NOT NULL, draft jsonb, content text, created_at timestamptz NOT NULL DEFAULT now(),
 enriched boolean NOT NULL DEFAULT false, lease_token uuid, lease_until timestamptz,
 UNIQUE(day,geo,channel)
);
CREATE TABLE IF NOT EXISTS trend_feed_calls (
 id text PRIMARY KEY, digest_id uuid NOT NULL REFERENCES trend_feed_digests(id),
 stage integer NOT NULL CHECK(stage IN (0,1)), request jsonb NOT NULL, response jsonb,
 state text NOT NULL DEFAULT 'running', error text, next_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 UNIQUE(digest_id,stage)
);
-- Preserve existing08:00 IDs and rows; later slots and explicit requests have their own keys.
ALTER TABLE trend_feed_digests ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'scheduled';
ALTER TABLE trend_feed_digests ADD COLUMN IF NOT EXISTS event_key text;
ALTER TABLE trend_feed_digests ADD COLUMN IF NOT EXISTS thread_ts text;
ALTER TABLE trend_feed_digests ADD COLUMN IF NOT EXISTS requested_at timestamptz;
ALTER TABLE trend_feed_digests ADD COLUMN IF NOT EXISTS reused_digest_id uuid REFERENCES trend_feed_digests(id);
ALTER TABLE trend_feed_digests DROP CONSTRAINT IF EXISTS trend_feed_digests_day_geo_channel_key;
CREATE UNIQUE INDEX IF NOT EXISTS trend_digest_scheduled_slot ON trend_feed_digests(send_at,geo,channel)
 WHERE kind='scheduled';
CREATE UNIQUE INDEX IF NOT EXISTS trend_digest_request_event ON trend_feed_digests(event_key)
 WHERE event_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS trend_feed_api_usage (
 day date PRIMARY KEY, calls integer NOT NULL DEFAULT 0 CHECK(calls>=0)
);
CREATE TABLE IF NOT EXISTS trend_feed_api_receipts (
 id text PRIMARY KEY, digest_id uuid NOT NULL REFERENCES trend_feed_digests(id),
 kind text NOT NULL, request jsonb NOT NULL, receipt jsonb NOT NULL,
 checked_at timestamptz NOT NULL DEFAULT now()
);
