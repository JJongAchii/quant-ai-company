CREATE TABLE IF NOT EXISTS trading_signals (
 id uuid PRIMARY KEY, session_day date NOT NULL, kind text NOT NULL CHECK(kind IN ('intraday','overnight')),
 channel text NOT NULL, owner_user text NOT NULL,
 snapshot_at timestamptz NOT NULL, snapshot_latest timestamptz NOT NULL, tools_until timestamptz NOT NULL,
 fallback_at timestamptz NOT NULL, send_at timestamptz NOT NULL, entry_at timestamptz NOT NULL,
 exit_at timestamptz NOT NULL, exit_day date NOT NULL,
 state text NOT NULL DEFAULT 'scheduled'
  CHECK(state IN ('scheduled','asked','committed','fallback_sent','committed_late')),
 project_id uuid REFERENCES projects(id), task_id uuid, snapshot jsonb, snapshot_source_id text,
 baselines jsonb, proposal jsonb, timing text CHECK(timing IN ('on_time','late','after_entry')),
 committed_at timestamptz, unparsed boolean NOT NULL DEFAULT false,
 analyst_message_id uuid, fallback_message_id uuid, unparsed_message_id uuid,
 fallback_reason text, fallback_sent_at timestamptz,
 format_failures integer NOT NULL DEFAULT 0, fault_retries integer NOT NULL DEFAULT 0,
 last_error text, retry_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(session_day, kind)
);
CREATE TABLE IF NOT EXISTS trading_signal_returns (
 signal_id uuid NOT NULL REFERENCES trading_signals(id),
 instrument text NOT NULL CHECK(instrument IN ('KOSPI','KODEX200')),
 state text NOT NULL CHECK(state IN ('final','pending','excluded')),
 entry_price numeric, exit_price numeric, long_bp numeric, detail text, receipt jsonb,
 reported_on date, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(signal_id, instrument)
);
CREATE TABLE IF NOT EXISTS trading_signal_reports (
 day date PRIMARY KEY, project_id uuid REFERENCES projects(id), message_id uuid,
 summary jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
