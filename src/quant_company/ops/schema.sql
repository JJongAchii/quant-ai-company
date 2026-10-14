CREATE TABLE IF NOT EXISTS ops_host_snapshots (
 id smallint PRIMARY KEY CHECK(id=1), collected_at timestamptz NOT NULL, payload jsonb NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_incidents (
 id uuid PRIMARY KEY, key text NOT NULL, episode integer NOT NULL CHECK(episode>0),
 kind text NOT NULL CHECK(kind IN ('condition','event')),
 severity text NOT NULL CHECK(severity IN ('critical','warning')),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','active','closed')),
 title text NOT NULL, detail jsonb NOT NULL, sightings integer NOT NULL DEFAULT 1, clear_ticks integer NOT NULL DEFAULT 0,
 announced_severity text, project_id uuid REFERENCES projects(id), root_message_id uuid,
 first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
 UNIQUE(key,episode)
);
CREATE UNIQUE INDEX IF NOT EXISTS ops_incident_open ON ops_incidents(key) WHERE state<>'closed';
CREATE TABLE IF NOT EXISTS ops_publications (
 id uuid PRIMARY KEY REFERENCES messages(id), incident_id uuid NOT NULL REFERENCES ops_incidents(id),
 kind text NOT NULL CHECK(kind IN ('root','escalation','recovery')), expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS ops_watch_state (
 id smallint PRIMARY KEY CHECK(id=1), ticked_at timestamptz NOT NULL, summary jsonb NOT NULL
);
