CREATE TABLE IF NOT EXISTS quant_feed_sources (
 id text PRIMARY KEY, config jsonb NOT NULL, config_digest text NOT NULL, enabled boolean NOT NULL,
 next_at timestamptz NOT NULL DEFAULT now(), lease_token uuid, lease_until timestamptz,
 last_attempt timestamptz, last_success timestamptz, failures integer NOT NULL DEFAULT 0,
 error text, receipt jsonb
);
CREATE TABLE IF NOT EXISTS quant_feed_candidates (
 id text PRIMARY KEY, source_id text NOT NULL REFERENCES quant_feed_sources(id), url text UNIQUE NOT NULL,
 title text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}', source_digest text NOT NULL,
 state text NOT NULL DEFAULT 'collected', discovered_at timestamptz NOT NULL DEFAULT now(),
 next_at timestamptz NOT NULL DEFAULT now(), lease_token uuid, lease_until timestamptz,
 last_fetch timestamptz, error text, receipt jsonb
);
CREATE TABLE IF NOT EXISTS quant_feed_works (id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS quant_feed_aliases (
 alias text PRIMARY KEY, work_id text NOT NULL REFERENCES quant_feed_works(id)
);
CREATE TABLE IF NOT EXISTS quant_feed_documents (
 id text PRIMARY KEY, work_id text NOT NULL REFERENCES quant_feed_works(id),
 candidate_id text NOT NULL REFERENCES quant_feed_candidates(id), source_digest text NOT NULL,
 content_digest text NOT NULL, metadata jsonb NOT NULL, receipt jsonb NOT NULL, pages jsonb NOT NULL,
 state text NOT NULL DEFAULT 'ready', stage text NOT NULL DEFAULT 'review', revision integer NOT NULL DEFAULT 0,
 brief jsonb, critique jsonb, error text, created_at timestamptz NOT NULL DEFAULT now(), reviewed_at timestamptz,
 UNIQUE(work_id,content_digest)
);
CREATE TABLE IF NOT EXISTS quant_feed_calls (
 id text PRIMARY KEY, document_id text REFERENCES quant_feed_documents(id), stage text NOT NULL,
 slot text, request jsonb NOT NULL, bundle jsonb NOT NULL, policy_digest text NOT NULL,
 state text NOT NULL DEFAULT 'running', next_at timestamptz NOT NULL DEFAULT now(),
 response jsonb, receipt jsonb, error text, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 UNIQUE(stage,slot)
);
CREATE UNIQUE INDEX IF NOT EXISTS quant_feed_document_call ON quant_feed_calls(document_id)
 WHERE state='running';
CREATE TABLE IF NOT EXISTS quant_feed_publications (
 id uuid PRIMARY KEY REFERENCES messages(id), document_id text NOT NULL REFERENCES quant_feed_documents(id),
 work_id text NOT NULL REFERENCES quant_feed_works(id), channel text NOT NULL, policy_digest text NOT NULL,
 correction boolean NOT NULL DEFAULT false, previous_id uuid REFERENCES quant_feed_publications(id),
 UNIQUE(channel,document_id)
);
CREATE TABLE IF NOT EXISTS quant_feed_delivery (channel text PRIMARY KEY, next_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS quant_feed_candidates_due ON quant_feed_candidates(next_at);
CREATE INDEX IF NOT EXISTS quant_feed_candidates_source_fetch ON quant_feed_candidates(source_id,last_fetch);
CREATE INDEX IF NOT EXISTS quant_feed_documents_pending ON quant_feed_documents(state,created_at);
