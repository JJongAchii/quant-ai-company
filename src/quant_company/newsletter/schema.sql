-- Brief email (docs/newsletter.md). Recipient addresses live only in these tables; logs, Slack and receipts
-- show them masked. 'removed' is the owner's choice, 'unsubscribed' the recipient's own and only 재동의 lifts it.
CREATE TABLE IF NOT EXISTS newsletter_recipients (
 id uuid PRIMARY KEY, address text NOT NULL UNIQUE CHECK(address = lower(address)),
 state text NOT NULL CHECK(state IN ('active','removed','unsubscribed')),
 unsubscribed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
-- One email per AM or PM edition. auto: created by its commit with NEWSLETTER_ENABLED (goes to every active recipient);
-- otherwise created by a test command and sent only to the addresses those commands name.
CREATE TABLE IF NOT EXISTS newsletter_issues (
 id uuid PRIMARY KEY, edition_id uuid NOT NULL UNIQUE REFERENCES brief_editions(id),
 state text NOT NULL CHECK(state IN ('writing','ready','sending','sent','skipped')),
 auto boolean NOT NULL, source jsonb, source_digest text, reason text,
 max_attempts integer NOT NULL CHECK(max_attempts>0), attempts jsonb NOT NULL DEFAULT '[]'::jsonb, request jsonb,
 -- The turn a fallback replaced while it may still run on claude-runtime: the writer cancels it, its output is never used.
 cancel_request text,
 draft jsonb, origin text CHECK(origin IN ('model','fallback')), findings jsonb NOT NULL DEFAULT '[]'::jsonb,
 fallback_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 next_at timestamptz NOT NULL DEFAULT now(), lease_until timestamptz,
 notice_message_id uuid REFERENCES messages(id),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
-- One row per recipient and email, written as 'sending' before the SMTP session talks about it. SMTP has no
-- idempotency key: a row interrupted from the DATA phase on, or left 'sending', becomes 'uncertain' and is never sent
-- again automatically. The Message-ID is stable per (edition, recipient).
CREATE TABLE IF NOT EXISTS newsletter_sends (
 id uuid PRIMARY KEY, issue_id uuid NOT NULL REFERENCES newsletter_issues(id),
 recipient_id uuid REFERENCES newsletter_recipients(id), address text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('regular','test')), message_id text NOT NULL UNIQUE,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','sent','uncertain','failed','skipped')),
 attempts integer NOT NULL DEFAULT 0, error text, project_id uuid REFERENCES projects(id),
 next_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, sent_at timestamptz, receipt jsonb,
 notice_message_id uuid REFERENCES messages(id),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS newsletter_sends_regular ON newsletter_sends(issue_id,recipient_id) WHERE kind='regular';
CREATE INDEX IF NOT EXISTS newsletter_sends_due ON newsletter_sends(state,next_at);
CREATE TABLE IF NOT EXISTS newsletter_commands (
 event_key text PRIMARY KEY, owner_user text NOT NULL, project_id uuid REFERENCES projects(id),
 action text NOT NULL, recipient_id uuid REFERENCES newsletter_recipients(id), receipt jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
-- Unsubscribe mails read from the sender's inbox, keyed by a digest of their Message-ID.
CREATE TABLE IF NOT EXISTS newsletter_unsubscribes (
 mail_digest text PRIMARY KEY, recipient_id uuid NOT NULL REFERENCES newsletter_recipients(id),
 notice_message_id uuid REFERENCES messages(id), created_at timestamptz NOT NULL DEFAULT now()
);
