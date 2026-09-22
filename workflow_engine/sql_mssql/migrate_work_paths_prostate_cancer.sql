/*
  Migrate workflow context paths: /work/prostate-cancer -> /work/projects/prostate-cancer
  Azure SQL / MSSQL (wf schema). Run dry-run SELECTs first; backup tables before APPLY.
*/

-- DRY-RUN ---------------------------------------------------------------------
SELECT 'workflow_instance' AS tbl, COUNT(*) AS n
FROM wf.workflow_instance
WHERE context_json LIKE N'%/work/prostate-cancer%';

SELECT TOP (10) id, status, LEFT(context_json, 120) AS ctx_preview
FROM wf.workflow_instance
WHERE context_json LIKE N'%/work/prostate-cancer%'
ORDER BY id DESC;

SELECT 'node_execution (active)' AS tbl, COUNT(*) AS n
FROM wf.node_execution ne
INNER JOIN wf.workflow_instance wi ON wi.id = ne.workflow_instance_id
WHERE wi.status IN (N'CREATED', N'RUNNING', N'PAUSED')
  AND (
    ISNULL(CAST(ne.input_json AS NVARCHAR(MAX)), N'') LIKE N'%/work/prostate-cancer%'
    OR ISNULL(CAST(ne.output_json AS NVARCHAR(MAX)), N'') LIKE N'%/work/prostate-cancer%'
  );

-- Portal discovery (sample metadata stays relational; only path-like JSON columns)
SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name
FROM sys.columns c
INNER JOIN sys.tables t ON c.object_id = t.object_id
INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name IN (N'portal', N'wf')
  AND TYPE_NAME(c.user_type_id) IN (N'nvarchar', N'varchar', N'json')
ORDER BY 1, 2, 3;

-- APPLY -----------------------------------------------------------------------
BEGIN TRANSACTION;

UPDATE wf.workflow_instance
SET context_json = REPLACE(CAST(context_json AS NVARCHAR(MAX)),
  N'/work/prostate-cancer', N'/work/projects/prostate-cancer')
WHERE context_json LIKE N'%/work/prostate-cancer%';

UPDATE ne
SET input_json = REPLACE(CAST(ne.input_json AS NVARCHAR(MAX)),
  N'/work/prostate-cancer', N'/work/projects/prostate-cancer')
FROM wf.node_execution ne
INNER JOIN wf.workflow_instance wi ON wi.id = ne.workflow_instance_id
WHERE wi.status IN (N'CREATED', N'RUNNING', N'PAUSED')
  AND ne.input_json LIKE N'%/work/prostate-cancer%';

UPDATE ne
SET output_json = REPLACE(CAST(ne.output_json AS NVARCHAR(MAX)),
  N'/work/prostate-cancer', N'/work/projects/prostate-cancer')
FROM wf.node_execution ne
INNER JOIN wf.workflow_instance wi ON wi.id = ne.workflow_instance_id
WHERE wi.status IN (N'CREATED', N'RUNNING', N'PAUSED')
  AND ne.output_json LIKE N'%/work/prostate-cancer%';

COMMIT TRANSACTION;

-- VERIFY ----------------------------------------------------------------------
SELECT COUNT(*) AS remaining_workflow_instance
FROM wf.workflow_instance
WHERE context_json LIKE N'%/work/prostate-cancer%';
