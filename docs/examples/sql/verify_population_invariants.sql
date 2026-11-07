-- Read-only checks after minimal population (schema + cfg + workflows + queue row).
-- Usage:
--   psql -v ON_ERROR_STOP=1 -f docs/examples/sql/verify_population_invariants.sql

\set ON_ERROR_STOP on

-- Ledgers (release apply idempotency)
SELECT 'wf.schema_release' AS check, count(*)::bigint AS n FROM wf.schema_release;
SELECT 'wf.omics_schema_release' AS check, count(*)::bigint AS n FROM wf.omics_schema_release;

-- Domain programs: compiled graph should exist for SamplePrepPipeline
SELECT d.name AS workflow_def, v.id AS workflow_version_id, v.is_active, v.root_node_id IS NOT NULL AS has_root
FROM wf.workflow_def d
JOIN wf.workflow_version v ON v.workflow_def_id = d.id
WHERE d.name = 'SamplePrepPipeline' AND v.is_active
ORDER BY v.version_major DESC, v.version_minor DESC, v.id DESC
LIMIT 1;

-- Action catalog: at least one omics-namespace action registered
SELECT namespace, count(*)::bigint AS n
FROM wf.workflow_action
WHERE namespace IN ('omics', 'engine')
GROUP BY namespace
ORDER BY namespace;

-- cfg reference rows (after sync_cfg_profiles_and_action_catalog)
SELECT count(*)::bigint AS published_profiles
FROM cfg.pipeline_profile WHERE status = 'published';

SELECT count(*)::bigint AS published_procedures
FROM cfg.assay_procedure WHERE status = 'published';

-- Study start queue (after enqueue_sample_prep_test_start.sql)
SELECT id, study_row_id, stage, status, workflow_version_id
FROM cfg.study_start_request
ORDER BY id DESC
LIMIT 3;
