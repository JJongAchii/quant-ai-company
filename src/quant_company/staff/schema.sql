-- Additive: operational conversations and prior role permissions remain intact.
CREATE TABLE IF NOT EXISTS staff_runs (
 id uuid PRIMARY KEY, owner_user text NOT NULL, employee text NOT NULL,
 purpose text NOT NULL CHECK(purpose IN ('scheduled','baseline','manual')),
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','completed','blocked','disputed')),
 model text NOT NULL, role_snapshot jsonb NOT NULL, pack_snapshot jsonb NOT NULL,
 code_commit text NOT NULL, suite_version text NOT NULL, family_index integer NOT NULL,
 public_case jsonb NOT NULL, answer_key jsonb NOT NULL, case_digest text NOT NULL,
 max_calls integer NOT NULL CHECK(max_calls BETWEEN 1 AND 4),
 schedule_day date, next_at timestamptz NOT NULL DEFAULT now(),
 grade jsonb, final_answer jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS staff_runs_queue ON staff_runs(state,next_at);
CREATE INDEX IF NOT EXISTS staff_runs_owner ON staff_runs(owner_user,employee,created_at);
ALTER TABLE staff_runs ADD COLUMN IF NOT EXISTS curriculum jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS staff_calls (
 id text PRIMARY KEY, run_id uuid NOT NULL REFERENCES staff_runs(id), sequence integer NOT NULL,
 request jsonb NOT NULL, response jsonb, tools jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 UNIQUE(run_id,sequence)
);
CREATE TABLE IF NOT EXISTS staff_feedback (
 id uuid PRIMARY KEY, run_id uuid UNIQUE NOT NULL REFERENCES staff_runs(id),
 owner_user text NOT NULL, employee text NOT NULL, family text NOT NULL,
 weaknesses jsonb NOT NULL, practice_advice text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS staff_reviews (
 id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES staff_runs(id),
 reviewer text NOT NULL, disposition text NOT NULL CHECK(disposition IN ('confirmed','disputed')),
 note text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

-- Held-out comparison inputs stay outside maintenance payloads, prompts and employee retrieval.
CREATE TABLE IF NOT EXISTS staff_comparisons (
 job_id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES staff_runs(id), owner_user text NOT NULL,
 employee text NOT NULL, base_commit text NOT NULL, material jsonb NOT NULL, input_digest text NOT NULL,
 candidate_digest text, results jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
