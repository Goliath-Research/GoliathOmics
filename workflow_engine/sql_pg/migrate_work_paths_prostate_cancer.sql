-- Migrate workflow context paths: /work/prostate-cancer -> /work/projects/prostate-cancer
-- PostgreSQL (wf schema). Run dry-run SELECTs first; backup tables before APPLY.

-- DRY-RUN ---------------------------------------------------------------------
SELECT 'workflow_instance' AS tbl, COUNT(*) AS n
FROM wf.workflow_instance
WHERE context_json::text LIKE '%/work/prostate-cancer%';

SELECT id, status, left(context_json::text, 120) AS ctx_preview
FROM wf.workflow_instance
WHERE context_json::text LIKE '%/work/prostate-cancer%'
ORDER BY id DESC
LIMIT 10;

SELECT 'node_execution (active)' AS tbl, COUNT(*) AS n
FROM wf.node_execution ne
JOIN wf.workflow_instance wi ON wi.id = ne.workflow_instance_id
WHERE wi.status IN ('CREATED', 'RUNNING', 'PAUSED')
  AND (
    coalesce(ne.input_json::text, '') LIKE '%/work/prostate-cancer%'
    OR coalesce(ne.output_json::text, '') LIKE '%/work/prostate-cancer%'
  );

SELECT 'scope_variable' AS tbl, COUNT(*) AS n
FROM wf.scope_variable
WHERE context_value_json::text LIKE '%/work/prostate-cancer%';

-- Portal discovery (if portal schema exists)
SELECT table_schema, table_name, column_name
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
  AND data_type IN ('json', 'jsonb', 'text', 'character varying')
  AND table_name NOT LIKE 'pg_%'
ORDER BY 1, 2, 3;

-- APPLY (wrap in transaction on operator host) --------------------------------
BEGIN;

UPDATE wf.workflow_instance
SET context_json = replace(context_json::text,
  '/work/prostate-cancer', '/work/projects/prostate-cancer')::jsonb
WHERE context_json::text LIKE '%/work/prostate-cancer%';

UPDATE wf.node_execution ne
SET input_json = replace(ne.input_json::text,
      '/work/prostate-cancer', '/work/projects/prostate-cancer')::jsonb
FROM wf.workflow_instance wi
WHERE wi.id = ne.workflow_instance_id
  AND wi.status IN ('CREATED', 'RUNNING', 'PAUSED')
  AND ne.input_json::text LIKE '%/work/prostate-cancer%';

UPDATE wf.node_execution ne
SET output_json = replace(ne.output_json::text,
      '/work/prostate-cancer', '/work/projects/prostate-cancer')::jsonb
FROM wf.workflow_instance wi
WHERE wi.id = ne.workflow_instance_id
  AND wi.status IN ('CREATED', 'RUNNING', 'PAUSED')
  AND ne.output_json::text LIKE '%/work/prostate-cancer%';

UPDATE wf.scope_variable
SET context_value_json = replace(context_value_json::text,
  '/work/prostate-cancer', '/work/projects/prostate-cancer')::jsonb
WHERE context_value_json::text LIKE '%/work/prostate-cancer%';

COMMIT;

-- VERIFY ----------------------------------------------------------------------
SELECT COUNT(*) AS remaining_workflow_instance
FROM wf.workflow_instance
WHERE context_json::text LIKE '%/work/prostate-cancer%';
