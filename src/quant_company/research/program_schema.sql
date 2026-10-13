CREATE TABLE IF NOT EXISTS research_programs (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id), owner_user text NOT NULL,
 revision integer NOT NULL, spec jsonb NOT NULL, manifest_digest text NOT NULL,
 state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','active','paused','cancelled')),
 approval_event_id text UNIQUE REFERENCES inbound(event_key), approved_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project_id,revision,manifest_digest), CHECK(state <> 'active' OR approval_event_id IS NOT NULL)
);
ALTER TABLE research_missions ADD COLUMN IF NOT EXISTS program_id uuid REFERENCES research_programs(id);
-- A single authenticated program approval authorizes multiple bounded child missions.
ALTER TABLE research_missions DROP CONSTRAINT IF EXISTS research_missions_approval_event_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS research_direct_mission_approval ON research_missions(approval_event_id)
 WHERE program_id IS NULL;
ALTER TABLE research_mission_stages ALTER COLUMN mission_id DROP NOT NULL;
ALTER TABLE research_mission_stages ADD COLUMN IF NOT EXISTS program_id uuid REFERENCES research_programs(id);
CREATE UNIQUE INDEX IF NOT EXISTS research_program_stage_key ON research_mission_stages(program_id,stage_key)
 WHERE program_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS research_program_tasks (
 id uuid PRIMARY KEY, program_id uuid NOT NULL REFERENCES research_programs(id),
 proposal jsonb NOT NULL, digest text NOT NULL, data_assessment jsonb, decision jsonb,
 state text NOT NULL DEFAULT 'proposed' CHECK(state IN ('proposed','assessed','accepted','rejected','waiting')),
 mission_id uuid UNIQUE REFERENCES research_missions(id), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(program_id,digest)
);
CREATE TABLE IF NOT EXISTS research_program_reservations (
 job_id uuid PRIMARY KEY REFERENCES research_jobs(id), program_id uuid NOT NULL REFERENCES research_programs(id),
 trial_id uuid NOT NULL REFERENCES research_mission_trials(id),
 reserved_seconds integer NOT NULL CHECK(reserved_seconds>0),
 actual_seconds integer CHECK(actual_seconds>=0), scientific_trial boolean,
 receipt_digest text, created_at timestamptz NOT NULL DEFAULT now(), settled_at timestamptz,
 CHECK ((settled_at IS NULL AND scientific_trial IS NULL AND actual_seconds IS NULL)
     OR (settled_at IS NOT NULL AND scientific_trial IS NOT NULL AND actual_seconds IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS research_literature (
 source_id text PRIMARY KEY REFERENCES sources(id), project_id uuid NOT NULL REFERENCES projects(id),
 document_id text NOT NULL REFERENCES quant_feed_documents(id), work_id text NOT NULL,
 content_digest text NOT NULL, state text NOT NULL CHECK(state IN ('current','superseded','retracted')),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(project_id,document_id)
);
CREATE TABLE IF NOT EXISTS research_challenge_responses (
 challenge_id uuid PRIMARY KEY REFERENCES research_mission_challenges(id),
 mission_id uuid NOT NULL REFERENCES research_missions(id), proposal_id uuid NOT NULL REFERENCES research_mission_proposals(id),
 payload jsonb NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_meaning_reviews (
 trial_id uuid PRIMARY KEY REFERENCES research_mission_trials(id), payload jsonb NOT NULL,
 digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_scientific_lineages (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 trial_limit integer NOT NULL CHECK(trial_limit>0), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_scientific_lineage_origins (
 task_id uuid PRIMARY KEY REFERENCES research_program_tasks(id),
 lineage_id uuid NOT NULL REFERENCES research_scientific_lineages(id), task_digest text NOT NULL
);
CREATE TABLE IF NOT EXISTS research_scientific_lineage_trials (
 trial_id uuid PRIMARY KEY REFERENCES research_mission_trials(id),
 lineage_id uuid NOT NULL REFERENCES research_scientific_lineages(id)
);
CREATE TABLE IF NOT EXISTS research_program_lineage_authorizations (
 program_id uuid NOT NULL REFERENCES research_programs(id), envelope text NOT NULL,
 lineage_id uuid NOT NULL REFERENCES research_scientific_lineages(id), authority_digest text NOT NULL,
 PRIMARY KEY(program_id,envelope)
);

CREATE TABLE IF NOT EXISTS research_scientific_lineage_experiments (
    project_id uuid NOT NULL REFERENCES projects(id),
    code_signature text NOT NULL CHECK (code_signature ~ '^[a-f0-9]{64}$'),
    lineage_id uuid NOT NULL REFERENCES research_scientific_lineages(id),
    trial_id uuid NOT NULL REFERENCES research_mission_trials(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, code_signature)
);
