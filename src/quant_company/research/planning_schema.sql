CREATE TABLE IF NOT EXISTS research_questions (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id), owner_user text NOT NULL,
 project_revision integer NOT NULL, program_id uuid REFERENCES research_programs(id),
 current_revision integer NOT NULL DEFAULT 1 CHECK(current_revision>=1), digest text NOT NULL, title text NOT NULL,
 state text NOT NULL DEFAULT 'proposed' CHECK(state IN ('proposed','open','waiting','paused','closed')),
 origin text NOT NULL CHECK(origin IN ('owner_request','follow_up','gap')),
 parent_question_id uuid REFERENCES research_questions(id), priority integer NOT NULL DEFAULT 0,
 activated_event_id text UNIQUE REFERENCES inbound(event_key), created_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(state NOT IN ('open','waiting') OR activated_event_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS research_questions_project_state ON research_questions(project_id,state);
CREATE TABLE IF NOT EXISTS research_question_revisions (
 question_id uuid NOT NULL REFERENCES research_questions(id), revision integer NOT NULL CHECK(revision>=1),
 payload jsonb NOT NULL, digest text NOT NULL, created_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(question_id,revision)
);
CREATE TABLE IF NOT EXISTS research_evidence_sets (
 id uuid PRIMARY KEY, question_id uuid NOT NULL, question_revision integer NOT NULL,
 program_id uuid REFERENCES research_programs(id), program_manifest_digest text,
 corpus_digest text NOT NULL, corpus_watermark timestamptz, policy_digest text NOT NULL,
 members jsonb NOT NULL, cards jsonb NOT NULL, notes jsonb NOT NULL DEFAULT '{}',
 char_total integer NOT NULL CHECK(char_total>=0), digest text NOT NULL,
 state text NOT NULL CHECK(state IN ('ready','oversize')), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(question_id,question_revision,corpus_digest,policy_digest),
 FOREIGN KEY(question_id,question_revision) REFERENCES research_question_revisions(question_id,revision)
);
CREATE TABLE IF NOT EXISTS research_gaps (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 question_id uuid REFERENCES research_questions(id), evidence_set_id uuid REFERENCES research_evidence_sets(id),
 kind text NOT NULL, subject text NOT NULL, subject_key text NOT NULL,
 resolver text NOT NULL CHECK(resolver IN ('employee','operator','owner')),
 required_evidence text NOT NULL, recheck_on text NOT NULL, dependency_fingerprint text NOT NULL,
 state text NOT NULL DEFAULT 'open' CHECK(state IN ('open','resolved','superseded','unresolvable')),
 resolution jsonb, root_gap_id uuid REFERENCES research_gaps(id),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS research_gaps_open_subject ON research_gaps(project_id,subject_key)
 WHERE state='open';
CREATE TABLE IF NOT EXISTS research_planning_runs (
 id uuid PRIMARY KEY, question_id uuid NOT NULL REFERENCES research_questions(id),
 question_revision integer NOT NULL, project_id uuid NOT NULL REFERENCES projects(id), owner_user text NOT NULL,
 evidence_set_id uuid NOT NULL REFERENCES research_evidence_sets(id), program_id uuid REFERENCES research_programs(id),
 program_manifest_digest text, mode text NOT NULL CHECK(mode IN ('shadow','active')),
 protocol text NOT NULL DEFAULT 'planning_v1' CHECK(protocol='planning_v1'), policy_digest text NOT NULL,
 trigger_key text NOT NULL UNIQUE, trigger jsonb NOT NULL,
 state text NOT NULL DEFAULT 'slate'
  CHECK(state IN ('slate','triaged','critique','decision','completed','held','superseded','cancelled')),
 outcome text CHECK(outcome IN ('handed_off','literature_only','remediation','awaiting_program','deferred',
  'rejected','no_candidate','shadow_recorded')),
 hold_reason text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(state <> 'completed' OR outcome IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS research_planning_runs_owner_state ON research_planning_runs(owner_user,state);
ALTER TABLE research_mission_stages ADD COLUMN IF NOT EXISTS planning_run_id uuid
 REFERENCES research_planning_runs(id);
CREATE UNIQUE INDEX IF NOT EXISTS research_planning_stage_key ON research_mission_stages(planning_run_id,stage_key)
 WHERE planning_run_id IS NOT NULL;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='research_planning_stage_scope') THEN
  ALTER TABLE research_mission_stages ADD CONSTRAINT research_planning_stage_scope
   CHECK(planning_run_id IS NULL OR (mission_id IS NULL AND program_id IS NULL));
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS research_planning_candidates (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES research_planning_runs(id),
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 3), payload jsonb NOT NULL, digest text NOT NULL,
 task_digest text, stage_id uuid NOT NULL REFERENCES research_mission_stages(id), attempt integer NOT NULL,
 route text, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run_id,ordinal), UNIQUE(run_id,digest)
);
CREATE TABLE IF NOT EXISTS research_planning_reviews (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES research_planning_runs(id),
 candidate_id uuid NOT NULL REFERENCES research_planning_candidates(id),
 kind text NOT NULL CHECK(kind IN ('triage','critique')), payload jsonb NOT NULL, digest text NOT NULL,
 stage_id uuid REFERENCES research_mission_stages(id), attempt integer,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(candidate_id,kind)
);
ALTER TABLE research_gaps ADD COLUMN IF NOT EXISTS run_id uuid REFERENCES research_planning_runs(id);
ALTER TABLE research_gaps ADD COLUMN IF NOT EXISTS candidate_id uuid REFERENCES research_planning_candidates(id);
CREATE TABLE IF NOT EXISTS research_planning_decisions (
 run_id uuid PRIMARY KEY REFERENCES research_planning_runs(id),
 stage_id uuid NOT NULL REFERENCES research_mission_stages(id), attempt integer NOT NULL,
 payload jsonb NOT NULL, service_order jsonb NOT NULL, override boolean NOT NULL, digest text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
