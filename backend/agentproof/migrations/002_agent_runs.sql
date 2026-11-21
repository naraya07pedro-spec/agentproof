CREATE TABLE agent_runs (
    id uuid PRIMARY KEY,
    owner text NOT NULL CHECK (length(owner) BETWEEN 1 AND 64),
    agent_id text NOT NULL,
    agent_name text NOT NULL,
    agent_version text,
    model text NOT NULL,
    suite_id text NOT NULL,
    suite_name text NOT NULL,
    suite_version text NOT NULL,
    dataset_digest text NOT NULL CHECK (dataset_digest ~ '^[a-f0-9]{64}$'),
    engine_version text NOT NULL,
    adapter_version text NOT NULL,
    evidence text NOT NULL CHECK (evidence IN ('local_reference_http', 'external_model_http')),
    passed boolean NOT NULL,
    case_count integer NOT NULL CHECK (case_count > 0),
    failed_count integer NOT NULL CHECK (failed_count BETWEEN 0 AND case_count),
    critical_failed integer NOT NULL CHECK (critical_failed BETWEEN 0 AND failed_count),
    metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (passed = (failed_count = 0))
);

CREATE INDEX agent_runs_owner_created ON agent_runs (owner, created_at DESC, id DESC);

CREATE TABLE agent_run_results (
    run_id uuid NOT NULL REFERENCES agent_runs (id),
    position integer NOT NULL CHECK (position >= 0),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    PRIMARY KEY (run_id, position)
);
