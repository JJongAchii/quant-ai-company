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
