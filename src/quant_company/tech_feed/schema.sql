CREATE TABLE IF NOT EXISTS tech_feed_sources (
 id text PRIMARY KEY, config jsonb NOT NULL, config_digest text NOT NULL, enabled boolean NOT NULL,
 etag text, modified text, next_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, lease_token uuid, initialized_at timestamptz,
 last_success timestamptz, last_attempt timestamptz, failures integer NOT NULL DEFAULT 0,
 error text, receipt jsonb
);
CREATE TABLE IF NOT EXISTS tech_feed_items (
 id text PRIMARY KEY, source_id text NOT NULL REFERENCES tech_feed_sources(id),
 guid text NOT NULL, url text NOT NULL, title text NOT NULL, description text NOT NULL,
 event_at timestamptz, time_kind text NOT NULL, source_digest text NOT NULL,
 collected_at timestamptz NOT NULL DEFAULT now(), state text NOT NULL,
 UNIQUE(source_id, url)
);
CREATE TABLE IF NOT EXISTS tech_feed_publications (
 id uuid PRIMARY KEY REFERENCES messages(id), item_id text NOT NULL REFERENCES tech_feed_items(id),
 channel text NOT NULL, owner_user text NOT NULL, url text NOT NULL,
 policy_digest text NOT NULL, expires_at timestamptz NOT NULL, UNIQUE(channel,url)
);
CREATE TABLE IF NOT EXISTS tech_feed_delivery (
 channel text PRIMARY KEY, next_at timestamptz NOT NULL
);
