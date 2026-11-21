CREATE TABLE suites (
    id uuid PRIMARY KEY,
    owner text NOT NULL CHECK (length(owner) BETWEEN 1 AND 64),
    name text NOT NULL,
    spec jsonb NOT NULL CHECK (jsonb_typeof(spec) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner, id)
);

CREATE INDEX suites_owner_created ON suites (owner, created_at DESC, id DESC);

CREATE TABLE runs (
    id uuid PRIMARY KEY,
    owner text NOT NULL,
    suite_id uuid NOT NULL,
    replay_of uuid,
    fixture text NOT NULL CHECK (fixture IN ('corrected', 'vulnerable')),
    engine_version text NOT NULL,
    spec_version text NOT NULL,
    evidence text NOT NULL CHECK (evidence = 'deterministic_simulation'),
    passed boolean NOT NULL,
    case_count integer NOT NULL CHECK (case_count > 0),
    failed_count integer NOT NULL CHECK (failed_count BETWEEN 0 AND case_count),
    critical_failed integer NOT NULL CHECK (critical_failed BETWEEN 0 AND failed_count),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (passed = (failed_count = 0)),
    CHECK (replay_of IS DISTINCT FROM id),
    UNIQUE (owner, suite_id, id),
    FOREIGN KEY (owner, suite_id) REFERENCES suites (owner, id),
    FOREIGN KEY (owner, suite_id, replay_of) REFERENCES runs (owner, suite_id, id)
);

CREATE INDEX runs_owner_created ON runs (owner, created_at DESC, id DESC);

CREATE TABLE run_results (
    run_id uuid NOT NULL REFERENCES runs (id),
    position integer NOT NULL CHECK (position >= 0),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    PRIMARY KEY (run_id, position)
);
