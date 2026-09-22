/*
  Register split-detector workflow actions (PostgreSQL, run once per wf database).

  Prerequisites:
    - wf schema deployed (workflow_engine/sql_pg/*.sql)
    - wf_action_dispatch_metadata.sql applied (7-arg upsert)

  Upserts (with dispatch metadata):
    pipeline.dmp_select          → methyl-dmp-select (cli)
    pipeline.gene_select         → methyl-gene-select (cli)
    pipeline.gene_feature_select → methyl-gene-feature-select (cli)
    validation.biomarker_filter  → validation.biomarker-filter (in_process)

  Prefer full catalog + task schemas (recommended for distributed workers):
    bash scripts/bootstrap_distributed_workers.sh --skip-schema --use-gateway-only

  This script is a lightweight alternative when you only need these four rows
  (Azure SQL equivalent: workflow_engine/sql_mssql/wf_split_detector_actions_seed.sql).
  It does NOT seed task I/O schemas under wf.workflow_action_schema.
*/

DO $$
BEGIN
  IF to_regprocedure(
    'wf.wf_repo_upsert_workflow_action(text,text,text,text,text,text,jsonb)'
  ) IS NULL THEN
    RAISE EXCEPTION
      'Prerequisite missing: deploy wf_action_dispatch_metadata.sql '
      '(7-arg wf_repo_upsert_workflow_action) first';
  END IF;
END $$;

CALL wf.wf_repo_upsert_workflow_action(
  'pipeline.dmp_select',
  'methyl-dmp-select',
  'pipeline.dmp_select',
  'cli',
  'methyl-dmp-select',
  NULL,
  '{"project":"--project","projectPath":"--project","group":"--group","chromosome":"--chromosome","discoveryCsv":"--discovery-csv","outputDir":"--output-dir","stepOverride":"--step-override"}'::jsonb
);
CALL wf.wf_repo_upsert_workflow_action(
  'pipeline.gene_select',
  'methyl-gene-select',
  'pipeline.gene_select',
  'cli',
  'methyl-gene-select',
  NULL,
  '{"project":"--project","projectPath":"--project","runDir":"--run-dir","biomarkerFilter":"--biomarker-filter"}'::jsonb
);
CALL wf.wf_repo_upsert_workflow_action(
  'pipeline.gene_feature_select',
  'methyl-gene-feature-select',
  'pipeline.gene_feature_select',
  'cli',
  'methyl-gene-feature-select',
  NULL,
  '{"mapperDir":"--mapper-dir","outputDir":"--output-dir"}'::jsonb
);
CALL wf.wf_repo_upsert_workflow_action(
  'validation.biomarker_filter',
  'validation.biomarker-filter',
  'validation.biomarker_filter',
  'in_process',
  NULL,
  '_handle_validation_biomarker_filter',
  NULL
);

DO $$
BEGIN
  RAISE NOTICE
    'Upserted split-detector actions (with dispatch metadata): '
    'pipeline.dmp_select, pipeline.gene_select, pipeline.gene_feature_select, '
    'validation.biomarker_filter';
END $$;
