/*
  Queue a SamplePrep test start on cfg.study_start_request (GoliathApp platform queue).

  Requires GoliathApp schema including cfg_study_start_request.sql (portal.sp_request_study_start).
  Does not create or start a workflow instance by itself: Python must drain the queue to bake
  context from materialized /work and call portal.sp_create_and_start_instance.

  psql variables (override before \i):
    study_row_id          cfg.study.id (published)
    workflow_version_id   wf.workflow_version.id; 0 = active SamplePrepPipeline
    project_path          request_json.projectPath (study manifest on /work)
    samples_json          JSON array; when empty, built from sample_id (+ optional paths)
    sample_id             used when samples_json is [] or unset
    sample_dir            optional per-sample sampleDir
    fastq_prefix          optional per-sample fastqPrefix
    fastq_storage_json    request_json.fastqStorage object
    sample_storage_json   optional request_json.sampleStorage (empty = omit)
    pipeline_profile      optional override (name)
    pipeline_procedure    optional override (name)
    created_by            audit label on the queue row

  Example:
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
      -v study_row_id=42 \
      -v workflow_version_id=0 \
      -v project_path='/work/projects/my-study/configs/project_my-study.json' \
      -v samples_json='[{"sampleId":"smoke-1","fastqPrefix":"smoke-1/"}]' \
      -v fastq_storage_json='{"type":"file","basePath":"/data/fastqs"}' \
      -f workflow_engine/sql_pg/enqueue_sample_prep_test_start.sql
*/

\set ON_ERROR_STOP on
-- Defaults (override with psql -v name=value or edit before \i)
\set study_row_id 0
\set workflow_version_id 0
\set project_path ''
\set samples_json '[]'
\set sample_id 'smoke-1'
\set sample_dir ''
\set fastq_prefix ''
\set fastq_storage_json '{"type":"file","basePath":"/data/fastqs"}'
\set sample_storage_json ''
\set pipeline_profile ''
\set pipeline_procedure ''
\set created_by 'psql-test-start'

DO $enqueue$
DECLARE
  v_study_row_id       bigint := :study_row_id;
  v_workflow_version_id bigint := :workflow_version_id;
  v_project_path       text := :'project_path';
  v_samples_json       text := :'samples_json';
  v_sample_id          text := :'sample_id';
  v_sample_dir         text := nullif(trim(:'sample_dir'), '');
  v_fastq_prefix       text := nullif(trim(:'fastq_prefix'), '');
  v_fastq_storage      jsonb := :'fastq_storage_json'::jsonb;
  v_sample_storage     jsonb;
  v_sample_storage_raw text := :'sample_storage_json';
  v_pipeline_profile   text := nullif(trim(:'pipeline_profile'), '');
  v_pipeline_procedure text := nullif(trim(:'pipeline_procedure'), '');
  v_created_by         text := nullif(trim(:'created_by'), '');

  v_preview            record;
  v_request            jsonb;
  v_samples            jsonb;
  v_extra              jsonb := '{}'::jsonb;
  v_result             record;
BEGIN
  IF v_study_row_id IS NULL OR v_study_row_id <= 0 THEN
    RAISE EXCEPTION 'Set study_row_id to a published cfg.study id (psql -v study_row_id=N)';
  END IF;
  IF nullif(trim(v_project_path), '') IS NULL THEN
    RAISE EXCEPTION 'Set project_path to the study manifest JSON on /work';
  END IF;

  IF v_workflow_version_id IS NULL OR v_workflow_version_id <= 0 THEN
    SELECT v.id
      INTO v_workflow_version_id
    FROM wf.workflow_def d
    JOIN wf.workflow_version v ON v.workflow_def_id = d.id
    WHERE d.name = 'SamplePrepPipeline'
      AND v.is_active
      AND v.root_node_id IS NOT NULL
    ORDER BY v.version_major DESC, v.version_minor DESC, v.id DESC
    LIMIT 1;
  END IF;

  IF v_workflow_version_id IS NULL OR v_workflow_version_id <= 0 THEN
    RAISE EXCEPTION 'No active SamplePrepPipeline workflow version; run deploy_workflow_definitions.sh or set workflow_version_id';
  END IF;

  SELECT *
    INTO v_preview
  FROM portal.sp_preview_study_start(
    v_study_row_id,
    'sample_prep',
    v_workflow_version_id,
    v_pipeline_profile,
    v_pipeline_procedure
  );

  IF nullif(trim(v_samples_json), '') IS NOT NULL AND v_samples_json <> '[]' THEN
    v_samples := v_samples_json::jsonb;
  ELSE
    IF nullif(trim(v_sample_id), '') IS NULL THEN
      RAISE EXCEPTION 'Provide samples_json or sample_id';
    END IF;
    v_samples := jsonb_build_array(
      jsonb_strip_nulls(
        jsonb_build_object(
          'sampleId', v_sample_id,
          'sampleDir', v_sample_dir,
          'fastqPrefix', v_fastq_prefix
        )
      )
    );
  END IF;

  IF nullif(trim(v_sample_storage_raw), '') IS NOT NULL
     AND lower(trim(v_sample_storage_raw)) <> 'null' THEN
    v_sample_storage := v_sample_storage_raw::jsonb;
    v_extra := v_extra || jsonb_build_object('sampleStorage', v_sample_storage);
  END IF;

  v_request :=
    coalesce(v_preview.request_json, '{}'::jsonb)
    || jsonb_build_object(
         'projectPath', v_project_path,
         'workflow_version_id', v_workflow_version_id,
         'samples', v_samples,
         'fastqStorage', v_fastq_storage
       )
    || v_extra;

  IF v_pipeline_profile IS NOT NULL THEN
    v_request := v_request || jsonb_build_object('pipelineProfile', v_pipeline_profile);
  END IF;
  IF v_pipeline_procedure IS NOT NULL THEN
    v_request := v_request || jsonb_build_object('pipelineProcedure', v_pipeline_procedure);
  END IF;

  SELECT *
    INTO v_result
  FROM portal.sp_request_study_start(
    v_study_row_id,
    'sample_prep',
    v_workflow_version_id,
    v_request,
    v_preview.pipeline_profile_id,
    v_preview.assay_procedure_id,
    v_preview.site_id,
    v_preview.storage_profile_id,
    coalesce(v_created_by, 'omics-sql/enqueue_sample_prep_test_start'),
    NULL
  );

  RAISE NOTICE 'Queued cfg.study_start_request id=% status=% workflow_version_id=%',
    v_result.request_id, v_result.status, v_result.workflow_version_id;
  RAISE NOTICE 'Drain with: methyl-study-start drain-requests (or python -m admin.study_start drain-requests)';
END;
$enqueue$;
