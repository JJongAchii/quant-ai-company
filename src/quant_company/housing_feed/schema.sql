CREATE TABLE IF NOT EXISTS housing_feed_sources (
 id text PRIMARY KEY, next_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, lease_until timestamptz, last_attempt timestamptz, last_success timestamptz,
 error text, receipt jsonb
);
CREATE TABLE IF NOT EXISTS housing_feed_notices (
 id text PRIMARY KEY, source_id text NOT NULL REFERENCES housing_feed_sources(id),
 payload jsonb NOT NULL, digest text NOT NULL, active boolean NOT NULL DEFAULT true,
 first_seen timestamptz NOT NULL DEFAULT now(), checked_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS housing_feed_publications (
 id uuid PRIMARY KEY REFERENCES messages(id), notice_id text NOT NULL REFERENCES housing_feed_notices(id),
 notice_digest text NOT NULL, channel text NOT NULL, event_key text NOT NULL,
 policy_digest text NOT NULL, expires_at timestamptz NOT NULL,
 UNIQUE(channel,event_key)
);
CREATE TABLE IF NOT EXISTS housing_feed_delivery (
 channel text PRIMARY KEY, next_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS housing_map_details (
 trigger_id text PRIMARY KEY, event_id text NOT NULL UNIQUE,
 notice_id text NOT NULL REFERENCES housing_feed_notices(id),
 channel text NOT NULL, message_ts text NOT NULL, user_id text NOT NULL,
 status text NOT NULL DEFAULT 'pending', error text,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz,
 expires_at timestamptz NOT NULL, attempts integer NOT NULL DEFAULT 0
);
