CREATE TABLE IF NOT EXISTS brief_editions (
 id uuid PRIMARY KEY, day date NOT NULL, kind text NOT NULL CHECK(kind IN ('am','pm')),
 channel text NOT NULL, owner_user text NOT NULL, definition jsonb NOT NULL,
 due_at timestamptz NOT NULL, cutoff timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 policy_digest text NOT NULL, publish boolean NOT NULL DEFAULT false,
 state text NOT NULL DEFAULT 'collecting', bundle jsonb, proposal jsonb, review jsonb,
 rendered jsonb, quality jsonb, error text, project_id uuid REFERENCES projects(id),
 collected_at timestamptz, collection_lease timestamptz, next_collection timestamptz NOT NULL DEFAULT now(),
 committed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(channel,owner_user,day,kind)
);
CREATE TABLE IF NOT EXISTS brief_calls (
 id text PRIMARY KEY, edition_id uuid NOT NULL REFERENCES brief_editions(id),
 phase text NOT NULL CHECK(phase IN ('search','write','review','revise','final_review')),
 request jsonb NOT NULL, response jsonb, result jsonb, state text NOT NULL DEFAULT 'running',
 next_at timestamptz NOT NULL DEFAULT now(), error text, completed_at timestamptz,
 UNIQUE(edition_id,phase)
);
CREATE TABLE IF NOT EXISTS brief_messages (
 id uuid PRIMARY KEY REFERENCES messages(id), edition_id uuid NOT NULL REFERENCES brief_editions(id),
 part integer NOT NULL, root_id uuid REFERENCES messages(id), policy_digest text NOT NULL,
 expires_at timestamptz NOT NULL, UNIQUE(edition_id,part)
);
CREATE INDEX IF NOT EXISTS brief_due ON brief_editions(state,due_at);
ALTER TABLE brief_editions ADD COLUMN IF NOT EXISTS market_data jsonb;
ALTER TABLE brief_editions ADD COLUMN IF NOT EXISTS data_lease timestamptz;
ALTER TABLE brief_editions ADD COLUMN IF NOT EXISTS next_data timestamptz NOT NULL DEFAULT now();
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='brief_calls'::regclass
            AND conname='brief_calls_phase_check' AND pg_get_constraintdef(oid) NOT LIKE '%revise%') THEN
  ALTER TABLE brief_calls DROP CONSTRAINT brief_calls_phase_check;
  ALTER TABLE brief_calls ADD CONSTRAINT brief_calls_phase_check
   CHECK(phase IN ('search','write','review','revise','final_review'));
 END IF;
END $$;
