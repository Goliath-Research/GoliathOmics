-- Minimal published cfg.study + cfg.site so portal.sp_preview_study_start (sample_prep) succeeds.
-- Prerequisites:
--   - GoliathApp + Omics schema applied
--   - Reference cfg from JSON (sync_cfg_profiles_and_action_catalog.py) including samd_research + buffy_wgbs_linear_gene_fc
--   - SamplePrepPipeline row in wf.workflow_version (deploy_workflow_definitions.sh)
--
-- Idempotent: cfg.cfg_repo_upsert is keyed by (name, version).
--
--   psql -v ON_ERROR_STOP=1 -f docs/examples/sql/minimal_study_for_sample_prep_enqueue.sql

\set ON_ERROR_STOP on

BEGIN;

SELECT id FROM cfg.cfg_repo_upsert(
  'site',
  'docsdemo_site',
  '1',
  'published',
  jsonb_build_object(
    'catalog', jsonb_build_object('title', 'Docs demo site', 'visibility', 'operator', 'lifecycle', 'active'),
    'displayName', 'Docs demo site'
  )
);

SELECT id FROM cfg.cfg_repo_upsert(
  'study',
  'docsdemo_study',
  '1',
  'published',
  jsonb_build_object(
    'pipelineProfile', 'samd_research',
    'pipelineProcedure', 'buffy_wgbs_linear_gene_fc',
    'researchMode', 'gene_fc',
    'primaryAnalyte', 'buffy_coat',
    'storage', jsonb_build_object(
      'fastqSourceEndpointId', NULL,
      'sampleDestinationEndpointId', NULL
    )
  ),
  NULL::jsonb,  -- p_secret
  NULL,         -- p_provider
  NULL,         -- p_auth_mode
  NULL,         -- p_credential_name
  'docsdemo-study-external-id'  -- p_study_id
);

COMMIT;

SELECT s.id AS study_row_id, s.name, s.status,
       s.document_json->>'pipelineProfile' AS pipeline_profile,
       s.document_json->>'pipelineProcedure' AS pipeline_procedure
FROM cfg.study s
WHERE s.name = 'docsdemo_study' AND s.version = '1';
