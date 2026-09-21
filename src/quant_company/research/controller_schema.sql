-- One durable staff step per mission stage. Technical retries do not consume trials.
CREATE TABLE IF NOT EXISTS research_mission_stages (
 id uuid PRIMARY KEY, mission_id uuid NOT NULL REFERENCES research_missions(id),
 stage_key text NOT NULL, stage text NOT NULL, actor text NOT NULL,
 context jsonb NOT NULL, task_id uuid REFERENCES tasks(id),
 attempt integer NOT NULL DEFAULT 0, state text NOT NULL DEFAULT 'pending'
 CHECK(state IN ('pending','running','received','completed','waiting','superseded')),
 result jsonb, error text, retry_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(mission_id,stage_key)
);
CREATE TABLE IF NOT EXISTS research_stage_attempts (
 stage_id uuid NOT NULL REFERENCES research_mission_stages(id), attempt integer NOT NULL,
 task_id uuid NOT NULL UNIQUE REFERENCES tasks(id), response jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 PRIMARY KEY(stage_id,attempt)
);
CREATE TABLE IF NOT EXISTS research_stage_reads (
 id uuid PRIMARY KEY, stage_id uuid NOT NULL REFERENCES research_mission_stages(id),
 attempt integer NOT NULL DEFAULT 1,
 path text NOT NULL, character_offset integer NOT NULL CHECK(character_offset>=0), content text NOT NULL,
 next_offset integer, sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE research_stage_reads ADD COLUMN IF NOT EXISTS attempt integer NOT NULL DEFAULT 1;
-- Multiple scientific trials inherit one mission approval; replay still permits one job per event.
ALTER TABLE research_jobs DROP CONSTRAINT IF EXISTS research_jobs_approval_event_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS research_replay_approval_event ON research_jobs(approval_event_id)
 WHERE recipe_id='kr-etf-p11-replay-v1';
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 0;
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS bundle_path text;
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS bundle_sha256 text;
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS mission_id uuid REFERENCES research_missions(id);
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS trial_id uuid;
