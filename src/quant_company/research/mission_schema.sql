CREATE TABLE IF NOT EXISTS research_missions (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id), owner_user text NOT NULL,
 revision integer NOT NULL CHECK (revision > 0), spec jsonb NOT NULL, manifest_digest text NOT NULL,
 state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','active','paused','cancelled')),
 approval_event_id text UNIQUE, approved_at timestamptz,
 cycle integer NOT NULL DEFAULT 1 CHECK (cycle > 0), cycle_trials integer NOT NULL DEFAULT 0,
 cumulative_trials integer NOT NULL DEFAULT 0, stagnant_trials integer NOT NULL DEFAULT 0,
 incumbent_trial_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project_id,revision,manifest_digest),
 CHECK (cycle_trials >= 0 AND cumulative_trials >= cycle_trials AND stagnant_trials >= 0),
 CHECK (state <> 'active' OR approval_event_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS research_mission_project ON research_missions(project_id,created_at);

CREATE TABLE IF NOT EXISTS research_mission_controls (
 event_key text PRIMARY KEY REFERENCES inbound(event_key), mission_id uuid NOT NULL REFERENCES research_missions(id),
 payload jsonb NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_proposals (
 id uuid PRIMARY KEY, mission_id uuid NOT NULL REFERENCES research_missions(id), cycle integer NOT NULL,
 payload jsonb NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_challenges (
 id uuid PRIMARY KEY, mission_id uuid NOT NULL REFERENCES research_missions(id),
 proposal_id uuid NOT NULL REFERENCES research_mission_proposals(id),
 payload jsonb NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_rejections (
 proposal_id uuid PRIMARY KEY REFERENCES research_mission_proposals(id),
 mission_id uuid NOT NULL REFERENCES research_missions(id), payload jsonb NOT NULL,
 digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_trials (
 id uuid PRIMARY KEY, mission_id uuid NOT NULL REFERENCES research_missions(id),
 proposal_id uuid NOT NULL UNIQUE REFERENCES research_mission_proposals(id), cycle integer NOT NULL,
 selection jsonb NOT NULL, selection_digest text NOT NULL,
 state text NOT NULL DEFAULT 'selected' CHECK (state IN
 ('selected','prepared','queued','running','technical_waiting','received','interpreted','reported')),
 plan jsonb, plan_digest text, job_id uuid REFERENCES research_jobs(id),
 result_id uuid, feasible boolean, ordinal integer,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(mission_id,ordinal)
);
CREATE UNIQUE INDEX IF NOT EXISTS research_mission_one_pending_trial ON research_mission_trials(mission_id)
 WHERE state <> 'reported';
CREATE TABLE IF NOT EXISTS research_mission_attempts (
 job_id uuid PRIMARY KEY REFERENCES research_jobs(id), trial_id uuid NOT NULL REFERENCES research_mission_trials(id),
 plan jsonb NOT NULL, plan_digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_outcomes (
 id uuid PRIMARY KEY, trial_id uuid NOT NULL REFERENCES research_mission_trials(id),
 job_id uuid NOT NULL UNIQUE REFERENCES research_jobs(id),
 kind text NOT NULL CHECK (kind IN ('result','technical_failure')),
 payload jsonb NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_interpretations (
 trial_id uuid PRIMARY KEY REFERENCES research_mission_trials(id), payload jsonb NOT NULL,
 digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_publications (
 trial_id uuid PRIMARY KEY REFERENCES research_mission_trials(id), payload jsonb NOT NULL,
 digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_mission_cycles (
 mission_id uuid NOT NULL REFERENCES research_missions(id), cycle integer NOT NULL,
 payload jsonb NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(mission_id,cycle)
);
