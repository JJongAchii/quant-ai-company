CREATE TABLE IF NOT EXISTS market_desk_revisions (
 revision text PRIMARY KEY, issue_id text NOT NULL, aliases text[] NOT NULL, day date NOT NULL,
 created_at timestamptz NOT NULL, packet jsonb NOT NULL, article jsonb NOT NULL, request jsonb,
 state text NOT NULL, next_at timestamptz NOT NULL, followups jsonb NOT NULL DEFAULT '[]'
);
ALTER TABLE market_desk_revisions ADD COLUMN IF NOT EXISTS followups jsonb NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS market_desk_publication (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), checked_at timestamptz NOT NULL,
 market_at timestamptz, filings_at timestamptz
);
CREATE INDEX IF NOT EXISTS market_desk_day ON market_desk_revisions(day, created_at);
CREATE INDEX IF NOT EXISTS news_recent_originals ON news_articles(published_at DESC) WHERE content IS NOT NULL;
