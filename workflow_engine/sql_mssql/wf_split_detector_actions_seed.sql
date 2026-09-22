/*
  Register split-detector workflow actions (run once per wf database).

  **Prefer full catalog seed for distributed workers:**
    source .venv/bin/activate
    methyl-export-task-schemas
    methyl-export-action-catalog
    python workflow_engine/sql_mssql/seed_action_catalog.py

  This script upserts four actions only (legacy lightweight path) including
  dispatch metadata columns from wf_action_dispatch_metadata.sql.
  It does NOT seed task I/O schemas under wf.workflow_action_schema.
*/

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

IF COL_LENGTH('wf.workflow_action', 'execution_mode') IS NULL
BEGIN
    RAISERROR(
        N'Prerequisite missing: deploy wf_action_dispatch_metadata.sql before this seed.',
        16,
        1
    );
    RETURN;
END
GO

MERGE wf.workflow_action AS t
USING (VALUES
  (
    N'pipeline.dmp_select',
    N'methyl-dmp-select',
    N'pipeline.dmp_select',
    N'cli',
    N'methyl-dmp-select',
    CAST(NULL AS nvarchar(256)),
    CAST(N'{"project":"--project","projectPath":"--project","group":"--group","chromosome":"--chromosome","discoveryCsv":"--discovery-csv","outputDir":"--output-dir","stepOverride":"--step-override"}' AS json)
  ),
  (
    N'pipeline.gene_select',
    N'methyl-gene-select',
    N'pipeline.gene_select',
    N'cli',
    N'methyl-gene-select',
    CAST(NULL AS nvarchar(256)),
    CAST(N'{"project":"--project","projectPath":"--project","runDir":"--run-dir","biomarkerFilter":"--biomarker-filter"}' AS json)
  ),
  (
    N'pipeline.gene_feature_select',
    N'methyl-gene-feature-select',
    N'pipeline.gene_feature_select',
    N'cli',
    N'methyl-gene-feature-select',
    CAST(NULL AS nvarchar(256)),
    CAST(N'{"mapperDir":"--mapper-dir","outputDir":"--output-dir"}' AS json)
  ),
  (
    N'validation.biomarker_filter',
    N'validation.biomarker-filter',
    N'validation.biomarker_filter',
    N'in_process',
    CAST(NULL AS nvarchar(256)),
    N'_handle_validation_biomarker_filter',
    CAST(NULL AS json)
  )
) AS s(
  action_name,
  capability,
  payload_schema_ref,
  execution_mode,
  cli_tool,
  in_process_handler,
  argv_map
)
ON t.action_name = s.action_name
WHEN NOT MATCHED THEN
  INSERT (
    action_name,
    capability,
    payload_schema_ref,
    execution_mode,
    cli_tool,
    in_process_handler,
    argv_map
  )
  VALUES (
    s.action_name,
    s.capability,
    s.payload_schema_ref,
    s.execution_mode,
    s.cli_tool,
    s.in_process_handler,
    s.argv_map
  )
WHEN MATCHED THEN
  UPDATE SET
    capability = s.capability,
    payload_schema_ref = s.payload_schema_ref,
    execution_mode = s.execution_mode,
    cli_tool = s.cli_tool,
    in_process_handler = s.in_process_handler,
    argv_map = s.argv_map;
GO

PRINT N'Upserted split-detector actions (with dispatch metadata): pipeline.dmp_select, pipeline.gene_select, pipeline.gene_feature_select, validation.biomarker_filter';
GO
