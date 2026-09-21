CREATE TABLE IF NOT EXISTS news_sources (
 id text PRIMARY KEY, config jsonb NOT NULL, config_digest text NOT NULL,
 enabled boolean NOT NULL, etag text, modified text, next_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, last_success timestamptz, last_attempt timestamptz,
 failures integer NOT NULL DEFAULT 0, error text, receipt jsonb
);
CREATE TABLE IF NOT EXISTS news_articles (
 id text PRIMARY KEY, source_id text NOT NULL REFERENCES news_sources(id),
 url text NOT NULL, title text NOT NULL, summary text NOT NULL, feed_digest text NOT NULL,
 published_at timestamptz, updated_at timestamptz, collected_at timestamptz NOT NULL DEFAULT now(),
 source_digest text NOT NULL, state text NOT NULL, next_at timestamptz NOT NULL DEFAULT now(),
 attempts integer NOT NULL DEFAULT 0, content text, retrieval jsonb, error text,
 UNIQUE(source_id,url,feed_digest)
);
CREATE INDEX IF NOT EXISTS news_article_queue ON news_articles(state,next_at);
ALTER TABLE news_articles ADD COLUMN IF NOT EXISTS screening jsonb;
CREATE TABLE IF NOT EXISTS news_triages (
 id text PRIMARY KEY, state text NOT NULL DEFAULT 'running',
 request jsonb NOT NULL, bundle jsonb NOT NULL, response jsonb,
 policy_digest text NOT NULL, next_at timestamptz NOT NULL DEFAULT now(), error text,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS news_reviews (
 id text PRIMARY KEY, state text NOT NULL DEFAULT 'running',
 request jsonb NOT NULL, bundle jsonb NOT NULL, response jsonb, result jsonb,
 policy_digest text NOT NULL, next_at timestamptz NOT NULL DEFAULT now(), error text,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS news_events (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id), headline text NOT NULL,
 root_message_id uuid, last_facts text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS news_publications (
 id uuid PRIMARY KEY REFERENCES messages(id), event_id uuid NOT NULL REFERENCES news_events(id),
 review_id text NOT NULL REFERENCES news_reviews(id), article_ids jsonb NOT NULL,
 policy_digest text NOT NULL, expires_at timestamptz NOT NULL, broadcast boolean NOT NULL DEFAULT false
);
ALTER TABLE news_publications ADD COLUMN IF NOT EXISTS broadcast boolean NOT NULL DEFAULT false;
ALTER TABLE news_publications ADD COLUMN IF NOT EXISTS morning_day date;
ALTER TABLE news_publications ADD COLUMN IF NOT EXISTS digest_id uuid;
CREATE TABLE IF NOT EXISTS news_digests (
 id uuid PRIMARY KEY REFERENCES messages(id), project_id uuid NOT NULL REFERENCES projects(id),
 day date NOT NULL, part integer NOT NULL DEFAULT 0, root_id uuid,
 channel text NOT NULL, owner_user text NOT NULL,
 member_ids jsonb NOT NULL, policy_digest text NOT NULL, expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(day,channel,owner_user,part)
);
ALTER TABLE outbox ALTER COLUMN thread_ts DROP NOT NULL;
CREATE TABLE IF NOT EXISTS news_searches (
 id text PRIMARY KEY, state text NOT NULL DEFAULT 'running',
 topic text NOT NULL, arguments jsonb NOT NULL, request jsonb NOT NULL,
 policy_digest text NOT NULL, response jsonb, receipt jsonb,
 next_at timestamptz NOT NULL DEFAULT now(), error text,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
