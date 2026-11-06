/*
  Queue a SamplePrep test start on cfg.study_start_request (GoliathApp platform queue).

  Requires GoliathApp schema including cfg_study_start_request.sql (portal.sp_request_study_start).
  Does not create or start a workflow instance by itself: Python must drain the queue to bake
  context from materialized /work and call portal.sp_create_and_start_instance.

  Implementation uses session settings (set_config) because psql :VAR substitution does not apply
  inside dollar-quoted DO bodies.

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
    psql -v ON_ERROR_STOP=1 \
      -v study_row_id=42 \
      -v workflow_version_id=0 \
      -v project_path='/work/projects/my-study/configs/project_my-study.json' \
      -v samples_json='[{"sampleId":"smoke-1","fastqPrefix":"smoke-1/"}]' \
      -v fastq_storage_json='{"type":"file","basePath":"/data/fastqs"}' \
      -f workflow_engine/sql_pg/enqueue_sample_prep_test_start.sql

  Requires psql with conditional meta-commands (`\\if :{?var}`), e.g. PostgreSQL 11+.
*/

\set ON_ERROR_STOP on
-- Defaults only when not passed on the psql command line (`psql -v name=value`), because an
-- unconditional `\set name ...` overwrites `-v` (psql applies `-v` first, then runs this file).
\if :{?study_row_id}
\else
\set study_row_id 0
\endif
\if :{?workflow_version_id}
\else
\set workflow_version_id 0
\endif
\if :{?project_path}
\else
\set project_path ''
\endif
\if :{?samples_json}
\else
\set samples_json '[]'
\endif
\if :{?sample_id}
\else
\set sample_id 'smoke-1'
\endif
\if :{?sample_dir}
\else
\set sample_dir ''
\endif
\if :{?fastq_prefix}
\else
\set fastq_prefix ''
\endif
\if :{?fastq_storage_json}
\else
\set fastq_storage_json '{"type":"file","basePath":"/data/fastqs"}'
\endif
\if :{?sample_storage_json}
\else
\set sample_storage_json ''
\endif
\if :{?pipeline_profile}
\else
\set pipeline_profile ''
\endif
\if :{?pipeline_procedure}
\else
\set pipeline_procedure ''
\endif
\if :{?created_by}
\else
\set created_by 'psql-test-start'
\endif

BEGIN;

SELECT set_config('app.enqueue_study_row_id', :'study_row_id', true);
SELECT set_config('app.enqueue_workflow_version_id', :'workflow_version_id', true);
SELECT set_config('app.enqueue_project_path', :'project_path', true);
SELECT set_config('app.enqueue_samples_json', :'samples_json', true);
SELECT set_config('app.enqueue_sample_id', :'sample_id', true);
SELECT set_config('app.enqueue_sample_dir', :'sample_dir', true);
SELECT set_config('app.enqueue_fastq_prefix', :'fastq_prefix', true);
SELECT set_config('app.enqueue_fastq_storage_json', :'fastq_storage_json', true);
SELECT set_config('app.enqueue_sample_storage_json', :'sample_storage_json', true);
SELECT set_config('app.enqueue_pipeline_profile', :'pipeline_profile', true);
SELECT set_config('app.enqueue_pipeline_procedure', :'pipeline_procedure', true);
SELECT set_config('app.enqueue_created_by', :'created_by', true);

DO $enqueue$
DECLARE
  v_study_row_id       bigint := nullif(current_setting('app.enqueue_study_row_id', true), '')::bigint;
  v_workflow_version_id bigint := nullif(current_setting('app.enqueue_workflow_version_id', true), '')::bigint;
  v_project_path       text := nullif(current_setting('app.enqueue_project_path', true), '');
  v_samples_json       text := coalesce(nullif(current_setting('app.enqueue_samples_json', true), ''), '[]');
  v_sample_id          text := nullif(current_setting('app.enqueue_sample_id', true), '');
  v_sample_dir         text := nullif(trim(current_setting('app.enqueue_sample_dir', true)), '');
  v_fastq_prefix       text := nullif(trim(current_setting('app.enqueue_fastq_prefix', true)), '');
  v_fastq_storage      jsonb := current_setting('app.enqueue_fastq_storage_json', true)::jsonb;
  v_sample_storage     jsonb;
  v_sample_storage_raw text := current_setting('app.enqueue_sample_storage_json', true);
  v_pipeline_profile   text := nullif(trim(current_setting('app.enqueue_pipeline_profile', true)), '');
  v_pipeline_procedure text := nullif(trim(current_setting('app.enqueue_pipeline_procedure', true)), '');
  v_created_by         text := nullif(trim(current_setting('app.enqueue_created_by', true)), '');

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

COMMIT;
