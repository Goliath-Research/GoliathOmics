-- 22_omics_run_alignment_objects.sql
--
-- Meta objects for sequencing runs and alignment results.
--
-- 15_* registers omics/SequencingRun (parent platform/Batch) and omics/AlignmentResult (parent
-- platform/Document), and the portal lists omics/SequencingRun objects under 'Sequencing runs'
-- (16_*). Until now nothing created them; the facts lived only in wf.node_execution rows. This
-- file adds:
--
--   * the fields of both classes; references are relations to omics/Sample, omics/Study and
--     omics/SequencingRun;
--   * omics_clinical.wf_meta_object_source: which workflow actions produce which class
--     (sample.download_fastq -> omics/SequencingRun; the alignment actions -> omics/AlignmentResult);
--   * omics_clinical.wf_meta_object_link: one row per source node execution and class, so
--     every writer is idempotent per wf row (tracked by omics_clinical.record_history);
--   * the writers, as SQL class methods of the two classes:
--       omics/SequencingRun.RecordFromNodeExecution   omics_clinical.sp_sequencing_run_record
--       omics/AlignmentResult.RecordFromNodeExecution omics_clinical.sp_alignment_result_record
--       <class>.Backfill                              omics_clinical.sp_run_alignment_backfill
--     They only read the node execution, its workflow instance, the sample row named by the
--     action's sampleId and the study linked to the instance (cfg.study_instance_link); nothing
--     is invented. Objects are written with "Meta".sp_obj_insert / sp_obj_update, so GoliathApp
--     H2 (ObjValuesHistory) records every change and the ledgers stay verifiable;
--   * an AFTER trigger on wf.node_execution that calls the writer when a source action
--     SUCCEEDS. A writer error never fails the engine's transaction: it is reported as a
--     WARNING and the row stays in omics_clinical.v_run_alignment_pending for the backfill
--     (scripts/omics_run_alignment_backfill.sh).
--
-- Timestamps: link rows are timestamptz (now()); object documents carry UTC ISO-8601 strings
-- ('YYYY-MM-DDTHH:MI:SSZ') taken from the node execution's timestamptz columns.
--
-- Idempotent. Run after GoliathApp's apply_schema.sh and files 01-21.

SET client_min_messages = warning;

-- Types ------------------------------------------------------------------------------------
SELECT omics_clinical.fn_meta_ensure_wf_type('omics/meta/int', 'int', '{"type": "integer"}'::jsonb);
SELECT omics_clinical.fn_meta_ensure_wf_type('omics/meta/string', 'string', '{"type": "string"}'::jsonb);
SELECT omics_clinical.fn_meta_ensure_wf_type(
  'omics/meta/datetime', 'datetime', '{"type": "string", "format": "date-time"}'::jsonb);
SELECT omics_clinical.fn_meta_ensure_wf_type(
  'omics/meta/string_list', 'array', '{"type": "array", "items": {"type": "string"}}'::jsonb);
SELECT omics_clinical.fn_meta_ensure_wf_type(
  'omics/ref/sample', 'int',
  '{"type": "integer", "x-goliath-relation": {"entity": "omics.sample", "class": "omics/Sample", "key": "legacy_sample_id"}}'::jsonb);
SELECT omics_clinical.fn_meta_ensure_wf_type(
  'omics/ref/study', 'int',
  '{"type": "integer", "x-goliath-relation": {"class": "omics/Study", "key": "cfg_study_row_id"}}'::jsonb);
SELECT omics_clinical.fn_meta_ensure_wf_type(
  'omics/ref/sequencing_run', 'int',
  '{"type": "integer", "x-goliath-relation": {"class": "omics/SequencingRun", "key": "source_node_execution_id"}}'::jsonb);

-- Fields -----------------------------------------------------------------------------------
DO $fields$
DECLARE
  v_run integer;
  v_aln integer;
  v_has_label boolean;
  f text[];
BEGIN
  SELECT c."ID" INTO v_run FROM "Meta"."Classes" c WHERE c."Name" = 'omics/SequencingRun';
  SELECT c."ID" INTO v_aln FROM "Meta"."Classes" c WHERE c."Name" = 'omics/AlignmentResult';
  IF v_run IS NULL OR v_aln IS NULL THEN
    RAISE NOTICE 'omics/SequencingRun or omics/AlignmentResult is missing (15_*); skipping run/alignment fields';
    RETURN;
  END IF;

  -- platform/Batch has no Label; give runs one (platform/Document already has it).
  SELECT EXISTS (SELECT 1 FROM "Meta"."VClassFieldsResolved" fr WHERE fr."ClassID" = v_run AND fr."Name" = 'Label')
  INTO v_has_label;
  IF NOT v_has_label THEN
    PERFORM omics_clinical.sp_meta_upsert_class_field(
      v_run, 'Label', 'omics/meta/string', 'string', '{"type": "string"}'::jsonb, false);
  END IF;

  FOREACH f SLICE 1 IN ARRAY ARRAY[
    ARRAY['source_node_execution_id', 'omics/meta/int', 'int', '{"type": "integer"}', 'true'],
    ARRAY['workflow_instance_id', 'omics/meta/int', 'int', '{"type": "integer"}', 'true'],
    ARRAY['sample_name', 'omics/meta/string', 'string', '{"type": "string"}', 'true'],
    ARRAY['sample_id', 'omics/ref/sample', 'int',
          '{"type": "integer", "x-goliath-relation": {"entity": "omics.sample", "class": "omics/Sample", "key": "legacy_sample_id"}}', 'false'],
    ARRAY['study_id', 'omics/ref/study', 'int',
          '{"type": "integer", "x-goliath-relation": {"class": "omics/Study", "key": "cfg_study_row_id"}}', 'false']
  ] LOOP
    PERFORM omics_clinical.sp_meta_upsert_class_field(v_run, f[1], f[2], f[3], f[4]::jsonb, f[5]::boolean);
    PERFORM omics_clinical.sp_meta_upsert_class_field(v_aln, f[1], f[2], f[3], f[4]::jsonb, f[5]::boolean);
  END LOOP;

  FOREACH f SLICE 1 IN ARRAY ARRAY[
    ARRAY['run_accession', 'omics/meta/string', 'string', '{"type": "string"}'],
    ARRAY['fastq_files', 'omics/meta/string_list', 'array', '{"type": "array", "items": {"type": "string"}}'],
    ARRAY['fastq_count', 'omics/meta/int', 'int', '{"type": "integer"}'],
    ARRAY['ingested_at_utc', 'omics/meta/datetime', 'datetime', '{"type": "string", "format": "date-time"}']
  ] LOOP
    PERFORM omics_clinical.sp_meta_upsert_class_field(v_run, f[1], f[2], f[3], f[4]::jsonb, false);
  END LOOP;

  FOREACH f SLICE 1 IN ARRAY ARRAY[
    ARRAY['sequencing_run_id', 'omics/ref/sequencing_run', 'int',
          '{"type": "integer", "x-goliath-relation": {"class": "omics/SequencingRun", "key": "source_node_execution_id"}}'],
    ARRAY['aligner_action', 'omics/meta/string', 'string', '{"type": "string"}'],
    ARRAY['align_engine', 'omics/meta/string', 'string', '{"type": "string"}'],
    ARRAY['bam_path', 'omics/meta/string', 'string', '{"type": "string"}'],
    ARRAY['output_dir', 'omics/meta/string', 'string', '{"type": "string"}'],
    ARRAY['qc_metrics_path', 'omics/meta/string', 'string', '{"type": "string"}'],
    ARRAY['completed_at_utc', 'omics/meta/datetime', 'datetime', '{"type": "string", "format": "date-time"}']
  ] LOOP
    PERFORM omics_clinical.sp_meta_upsert_class_field(v_aln, f[1], f[2], f[3], f[4]::jsonb, false);
  END LOOP;

  PERFORM "Meta".sp_class_regenerate_views(v_run);
  PERFORM "Meta".sp_class_regenerate_views(v_aln);
END
$fields$;

-- Source actions and links -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS omics_clinical.wf_meta_object_source (
  action_name text PRIMARY KEY,
  class_name text NOT NULL CHECK (class_name IN ('omics/SequencingRun', 'omics/AlignmentResult')),
  description text NOT NULL
);

COMMENT ON TABLE omics_clinical.wf_meta_object_source IS
  'Workflow actions whose successful node executions are recorded as Meta objects (22_omics_run_alignment_objects.sql).';

INSERT INTO omics_clinical.wf_meta_object_source (action_name, class_name, description) VALUES
  ('sample.download_fastq', 'omics/SequencingRun', 'FASTQ files of a sequencing run staged for a sample'),
  ('sample.parabricks_fq2bam', 'omics/AlignmentResult', 'Linear alignment (Clara Parabricks or GoliathAlign, by parabricks.engine)'),
  ('sample.parabricks_rna_fq2bam', 'omics/AlignmentResult', 'RNA alignment (Parabricks)'),
  ('sample.parabricks_giraffe', 'omics/AlignmentResult', 'Pangenome alignment (Giraffe)'),
  ('sample.docker_align', 'omics/AlignmentResult', 'Containerised alignment'),
  ('sample.methylgrapher_wgbs_align', 'omics/AlignmentResult', 'WGBS pangenome alignment (methylGrapher)')
ON CONFLICT (action_name) DO UPDATE SET
  class_name = EXCLUDED.class_name,
  description = EXCLUDED.description;

CREATE TABLE IF NOT EXISTS omics_clinical.wf_meta_object_link (
  source_node_execution_id bigint NOT NULL,
  class_name text NOT NULL CHECK (class_name IN ('omics/SequencingRun', 'omics/AlignmentResult')),
  meta_obj_id integer NOT NULL,
  doc_sha256 text NOT NULL,
  created_at_utc timestamptz NOT NULL DEFAULT now(),
  updated_at_utc timestamptz NULL,
  CONSTRAINT pk_omics_wf_meta_object_link PRIMARY KEY (source_node_execution_id, class_name),
  CONSTRAINT uq_omics_wf_meta_object_link_obj UNIQUE (meta_obj_id),
  -- No foreign key to wf.node_execution: run retention (GoliathApp H3) may purge the run,
  -- and the recorded object outlives it.
  CONSTRAINT fk_omics_wf_meta_object_link_obj FOREIGN KEY (meta_obj_id)
    REFERENCES "Meta"."Objs" ("ID") ON DELETE CASCADE
);

COMMENT ON TABLE omics_clinical.wf_meta_object_link IS
  'Source node execution -> Meta object (omics/SequencingRun, omics/AlignmentResult); one object per wf row and class.';

-- New tables: track them in omics_clinical.record_history now (20_* picks up the tables that
-- exist when it runs; these are created after it).
DO $history$
DECLARE
  t record;
BEGIN
  IF to_regprocedure('omics_clinical.fn_record_history()') IS NULL THEN
    RETURN;
  END IF;
  FOR t IN
    SELECT * FROM (VALUES
      ('omics_clinical.wf_meta_object_source'::regclass, ARRAY['action_name']),
      ('omics_clinical.wf_meta_object_link'::regclass, ARRAY['source_node_execution_id', 'class_name'])
    ) AS v(rel, pk)
  LOOP
    CONTINUE WHEN EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = t.rel AND tgname = 'trg_omics_record_history');
    EXECUTE format(
      'INSERT INTO omics_clinical.record_history (table_name, row_key, op, after_doc) '
      'SELECT %L, jsonb_build_object(%s), ''baseline'', to_jsonb(x) FROM %s x',
      t.rel::text,
      (SELECT string_agg(format('%L, x.%I', c, c), ', ') FROM unnest(t.pk) AS c),
      t.rel);
    EXECUTE format(
      'CREATE TRIGGER trg_omics_record_history AFTER INSERT OR UPDATE OR DELETE ON %s '
      'FOR EACH ROW EXECUTE FUNCTION omics_clinical.fn_record_history(%s)',
      t.rel, (SELECT string_agg(quote_literal(c), ', ') FROM unnest(t.pk) AS c));
  END LOOP;
END
$history$;

-- Helpers ----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION omics_clinical.fn_utc_iso(p_at timestamptz)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT to_char(p_at AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
$$;

-- The sequencing-run accession shared by every FASTQ file name (SRR/ERR/DRR), or NULL.
CREATE OR REPLACE FUNCTION omics_clinical.fn_fastq_run_accession(p_files text[])
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT CASE WHEN count(DISTINCT acc) = 1 AND count(*) = count(acc) THEN min(acc) END
  FROM (
    SELECT (regexp_match(regexp_replace(f, '^.*/', ''), '^((?:SRR|ERR|DRR)[0-9]+)(?:_[0-9])?\.f(?:ast)?q(?:\.gz)?$'))[1] AS acc
    FROM unnest(p_files) AS f
  ) s
  HAVING count(*) > 0;
$$;

-- The source row: a SUCCEEDED node execution of an action listed for p_class_name.
CREATE OR REPLACE FUNCTION omics_clinical.fn_run_alignment_source(p_node_execution_id bigint, p_class_name text)
RETURNS TABLE (
  node_execution_id bigint,
  workflow_instance_id bigint,
  action_name text,
  input_json jsonb,
  output_json jsonb,
  started_at_utc timestamptz,
  ended_at_utc timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, wf, omics_clinical, pg_temp
AS $$
  SELECT ne.id, ne.workflow_instance_id, wa.action_name::text, ne.input_json, ne.output_json,
         ne.started_at_utc, ne.ended_at_utc
  FROM wf.node_execution ne
  JOIN wf.workflow_node wn ON wn.id = ne.workflow_node_id
  JOIN wf.workflow_action wa ON wa.id = wn.workflow_action_id
  JOIN omics_clinical.wf_meta_object_source s ON s.action_name = wa.action_name
  WHERE ne.id = p_node_execution_id
    AND ne.status = 'SUCCEEDED'
    AND s.class_name = p_class_name;
$$;

-- Insert or refresh the object for one source row; returns the Meta object id.
CREATE OR REPLACE FUNCTION omics_clinical.sp_run_alignment_upsert(
  p_node_execution_id bigint,
  p_class_name text,
  p_doc jsonb,
  p_scope_id integer
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, "Meta", omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_obj_id integer;
  v_old_sha text;
  v_sha text := encode(sha256(convert_to(p_doc::text, 'UTF8')), 'hex');
BEGIN
  SELECT c."ID" INTO v_class_id FROM "Meta"."Classes" c WHERE c."Name" = p_class_name;
  IF v_class_id IS NULL THEN
    RAISE EXCEPTION '% Meta class is missing (apply 15_omics_meta_domain_classes.sql)', p_class_name;
  END IF;

  SELECT l.meta_obj_id, l.doc_sha256 INTO v_obj_id, v_old_sha
  FROM omics_clinical.wf_meta_object_link l
  WHERE l.source_node_execution_id = p_node_execution_id AND l.class_name = p_class_name
  FOR UPDATE;

  IF v_obj_id IS NULL THEN
    v_obj_id := "Meta".sp_obj_insert(v_class_id, p_doc);
    INSERT INTO omics_clinical.wf_meta_object_link (source_node_execution_id, class_name, meta_obj_id, doc_sha256)
    VALUES (p_node_execution_id, p_class_name, v_obj_id, v_sha);
  ELSIF v_old_sha IS DISTINCT FROM v_sha THEN
    PERFORM "Meta".sp_obj_update(v_obj_id, p_doc);
    UPDATE omics_clinical.wf_meta_object_link
    SET doc_sha256 = v_sha, updated_at_utc = now()
    WHERE source_node_execution_id = p_node_execution_id AND class_name = p_class_name;
  END IF;

  -- Same tenant scope as the study (or sample) it belongs to; never moved once scoped.
  IF p_scope_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM portal."ScopedMetaObjects" sm WHERE sm."ObjID" = v_obj_id
  ) THEN
    INSERT INTO portal."ScopedMetaObjects" ("ObjID", "ScopeID") VALUES (v_obj_id, p_scope_id);
  END IF;

  RETURN v_obj_id;
END;
$$;

-- Sample row, study row and scope for a workflow instance and sampleId.
CREATE OR REPLACE FUNCTION omics_clinical.fn_run_alignment_context(p_workflow_instance_id bigint, p_sample_name text)
RETURNS TABLE (sample_id integer, study_id bigint, scope_id integer)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, portal, cfg, omics_clinical, pg_temp
AS $$
  SELECT
    (SELECT s."ID" FROM portal."Samples" s WHERE s."SampleName" = p_sample_name ORDER BY s."ID" LIMIT 1),
    (SELECT sil.study_row_id FROM cfg.study_instance_link sil
      WHERE sil.workflow_instance_id = p_workflow_instance_id ORDER BY sil.id LIMIT 1),
    COALESCE(
      (SELECT l.scope_id FROM cfg.study_instance_link sil
         JOIN omics_clinical.study_meta_link l ON l.study_row_id = sil.study_row_id
        WHERE sil.workflow_instance_id = p_workflow_instance_id ORDER BY sil.id LIMIT 1),
      (SELECT sm."ScopeID" FROM portal."Samples" s
         JOIN portal."ScopedMetaObjects" sm ON sm."ObjID" = s."MetaObjectID"
        WHERE s."SampleName" = p_sample_name ORDER BY s."ID" LIMIT 1)
    );
$$;

-- Writers (class methods) ------------------------------------------------------------------
CREATE OR REPLACE FUNCTION omics_clinical.sp_sequencing_run_record(p_node_execution_id bigint)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, omics_clinical, pg_temp
AS $$
DECLARE
  v_src record;
  v_ctx record;
  v_sample text;
  v_files text[];
  v_doc jsonb;
BEGIN
  SELECT * INTO v_src FROM omics_clinical.fn_run_alignment_source(p_node_execution_id, 'omics/SequencingRun');
  IF NOT FOUND THEN
    RAISE EXCEPTION 'node execution % is not a succeeded sequencing-run ingest', p_node_execution_id;
  END IF;
  v_sample := NULLIF(btrim(COALESCE(v_src.output_json->>'sampleId', v_src.input_json->>'sampleId', '')), '');
  IF v_sample IS NULL THEN
    RAISE EXCEPTION 'node execution % has no sampleId', p_node_execution_id;
  END IF;
  SELECT array_agg(f ORDER BY ord) INTO v_files
  FROM jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(v_src.output_json->'fastqFiles') = 'array' THEN v_src.output_json->'fastqFiles' ELSE '[]'::jsonb END
  ) WITH ORDINALITY AS t(f, ord);
  SELECT * INTO v_ctx FROM omics_clinical.fn_run_alignment_context(v_src.workflow_instance_id, v_sample);

  v_doc := jsonb_strip_nulls(jsonb_build_object(
    'Label', COALESCE(omics_clinical.fn_fastq_run_accession(v_files), v_sample)
             || ' (run ' || v_src.workflow_instance_id || ', step ' || v_src.node_execution_id || ')',
    'source_node_execution_id', v_src.node_execution_id,
    'workflow_instance_id', v_src.workflow_instance_id,
    'sample_name', v_sample,
    'sample_id', v_ctx.sample_id,
    'study_id', v_ctx.study_id,
    'run_accession', omics_clinical.fn_fastq_run_accession(v_files),
    'fastq_files', to_jsonb(v_files),
    'fastq_count', COALESCE(cardinality(v_files), 0),
    'ingested_at_utc', omics_clinical.fn_utc_iso(v_src.ended_at_utc)
  ));
  RETURN omics_clinical.sp_run_alignment_upsert(v_src.node_execution_id, 'omics/SequencingRun', v_doc, v_ctx.scope_id);
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_alignment_result_record(p_node_execution_id bigint)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, omics_clinical, pg_temp
AS $$
DECLARE
  v_src record;
  v_ctx record;
  v_sample text;
  v_run_ne bigint;
  v_bam text;
  v_doc jsonb;
BEGIN
  SELECT * INTO v_src FROM omics_clinical.fn_run_alignment_source(p_node_execution_id, 'omics/AlignmentResult');
  IF NOT FOUND THEN
    RAISE EXCEPTION 'node execution % is not a succeeded alignment step', p_node_execution_id;
  END IF;
  v_sample := NULLIF(btrim(COALESCE(v_src.output_json->>'sampleId', v_src.input_json->>'sampleId', '')), '');
  IF v_sample IS NULL THEN
    RAISE EXCEPTION 'node execution % has no sampleId', p_node_execution_id;
  END IF;

  -- The run it aligned: the latest succeeded ingest of the same sample in the same workflow
  -- instance that ended before this step started. Record it first if it is not recorded yet.
  SELECT ne.id INTO v_run_ne
  FROM wf.node_execution ne
  JOIN wf.workflow_node wn ON wn.id = ne.workflow_node_id
  JOIN wf.workflow_action wa ON wa.id = wn.workflow_action_id
  JOIN omics_clinical.wf_meta_object_source s ON s.action_name = wa.action_name AND s.class_name = 'omics/SequencingRun'
  WHERE ne.workflow_instance_id = v_src.workflow_instance_id
    AND ne.status = 'SUCCEEDED'
    AND COALESCE(ne.output_json->>'sampleId', ne.input_json->>'sampleId') = v_sample
    AND (v_src.started_at_utc IS NULL OR ne.ended_at_utc IS NULL OR ne.ended_at_utc <= v_src.started_at_utc)
  ORDER BY ne.ended_at_utc DESC NULLS LAST, ne.id DESC
  LIMIT 1;
  IF v_run_ne IS NOT NULL THEN
    PERFORM omics_clinical.sp_sequencing_run_record(v_run_ne);
  END IF;

  SELECT * INTO v_ctx FROM omics_clinical.fn_run_alignment_context(v_src.workflow_instance_id, v_sample);
  v_bam := NULLIF(COALESCE(v_src.output_json->>'bamPath', v_src.output_json->'domainSample'->>'bamPath'), '');

  v_doc := jsonb_strip_nulls(jsonb_build_object(
    'Label', v_sample || ' ' || replace(v_src.action_name, 'sample.', '')
             || COALESCE(' ' || NULLIF(v_src.input_json->'resolvedConfig'->>'engine', ''), '')
             || ' (run ' || v_src.workflow_instance_id || ', step ' || v_src.node_execution_id || ')',
    'source_node_execution_id', v_src.node_execution_id,
    'workflow_instance_id', v_src.workflow_instance_id,
    'sample_name', v_sample,
    'sample_id', v_ctx.sample_id,
    'study_id', v_ctx.study_id,
    'sequencing_run_id', v_run_ne,
    'aligner_action', v_src.action_name,
    'align_engine', NULLIF(v_src.input_json->'resolvedConfig'->>'engine', ''),
    'bam_path', v_bam,
    'output_dir', NULLIF(COALESCE(v_src.output_json->'domainSample'->>'sampleDir', v_src.input_json->>'sampleDir'), ''),
    'qc_metrics_path', NULLIF(v_src.output_json->>'qcMetricsTar', ''),
    'completed_at_utc', omics_clinical.fn_utc_iso(v_src.ended_at_utc)
  ));
  RETURN omics_clinical.sp_run_alignment_upsert(v_src.node_execution_id, 'omics/AlignmentResult', v_doc, v_ctx.scope_id);
END;
$$;

-- Succeeded source rows that have no object yet.
CREATE OR REPLACE VIEW omics_clinical.v_run_alignment_pending AS
SELECT ne.id AS node_execution_id, ne.workflow_instance_id, wa.action_name::text AS action_name, s.class_name
FROM wf.node_execution ne
JOIN wf.workflow_node wn ON wn.id = ne.workflow_node_id
JOIN wf.workflow_action wa ON wa.id = wn.workflow_action_id
JOIN omics_clinical.wf_meta_object_source s ON s.action_name = wa.action_name
WHERE ne.status = 'SUCCEEDED'
  AND NOT EXISTS (
    SELECT 1 FROM omics_clinical.wf_meta_object_link l
    WHERE l.source_node_execution_id = ne.id AND l.class_name = s.class_name
  );

-- Backfill: record (or refresh) every succeeded source row; runs first, then alignments.
-- Returns per class: source rows, objects created, objects updated. Rerunning is a no-op.
CREATE OR REPLACE FUNCTION omics_clinical.sp_run_alignment_backfill()
RETURNS TABLE (class_name text, source_rows integer, created integer, updated integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, omics_clinical, pg_temp
AS $$
DECLARE
  v_class text;
  v_ne bigint;
  v_links_before integer;
  v_sha_before text;
  v_sha_after text;
BEGIN
  FOREACH v_class IN ARRAY ARRAY['omics/SequencingRun', 'omics/AlignmentResult'] LOOP
    class_name := v_class;
    source_rows := 0;
    created := 0;
    updated := 0;
    FOR v_ne IN
      SELECT ne.id
      FROM wf.node_execution ne
      JOIN wf.workflow_node wn ON wn.id = ne.workflow_node_id
      JOIN wf.workflow_action wa ON wa.id = wn.workflow_action_id
      JOIN omics_clinical.wf_meta_object_source s ON s.action_name = wa.action_name AND s.class_name = v_class
      WHERE ne.status = 'SUCCEEDED'
      ORDER BY ne.id
    LOOP
      source_rows := source_rows + 1;
      SELECT count(*), min(l.doc_sha256) INTO v_links_before, v_sha_before
      FROM omics_clinical.wf_meta_object_link l
      WHERE l.source_node_execution_id = v_ne AND l.class_name = v_class;
      IF v_class = 'omics/SequencingRun' THEN
        PERFORM omics_clinical.sp_sequencing_run_record(v_ne);
      ELSE
        PERFORM omics_clinical.sp_alignment_result_record(v_ne);
      END IF;
      SELECT l.doc_sha256 INTO v_sha_after
      FROM omics_clinical.wf_meta_object_link l
      WHERE l.source_node_execution_id = v_ne AND l.class_name = v_class;
      IF v_links_before = 0 THEN
        created := created + 1;
      ELSIF v_sha_after IS DISTINCT FROM v_sha_before THEN
        updated := updated + 1;
      END IF;
    END LOOP;
    RETURN NEXT;
  END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION omics_clinical.sp_run_alignment_upsert(bigint, text, jsonb, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION omics_clinical.sp_sequencing_run_record(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION omics_clinical.sp_alignment_result_record(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION omics_clinical.sp_run_alignment_backfill() FROM PUBLIC;
REVOKE ALL ON FUNCTION omics_clinical.fn_run_alignment_source(bigint, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION omics_clinical.fn_run_alignment_context(bigint, text) FROM PUBLIC;

-- Class methods ----------------------------------------------------------------------------
DO $methods$
DECLARE
  m text[];
  v_class integer;
BEGIN
  FOREACH m SLICE 1 IN ARRAY ARRAY[
    ARRAY['omics/SequencingRun', 'RecordFromNodeExecution', 'omics_clinical.sp_sequencing_run_record',
          'Record the sequencing run staged by a succeeded sample.download_fastq step (idempotent per step)'],
    ARRAY['omics/SequencingRun', 'Backfill', 'omics_clinical.sp_run_alignment_backfill',
          'Record every succeeded run ingest and alignment step that has no object yet; refresh changed ones'],
    ARRAY['omics/AlignmentResult', 'RecordFromNodeExecution', 'omics_clinical.sp_alignment_result_record',
          'Record the result of a succeeded alignment step, linked to its run, sample and study (idempotent per step)'],
    ARRAY['omics/AlignmentResult', 'Backfill', 'omics_clinical.sp_run_alignment_backfill',
          'Record every succeeded run ingest and alignment step that has no object yet; refresh changed ones']
  ] LOOP
    SELECT c."ID" INTO v_class FROM "Meta"."Classes" c WHERE c."Name" = m[1];
    CONTINUE WHEN v_class IS NULL;
    INSERT INTO "Meta"."Methods" ("ClassID", "Name", "Description", "ImplKind", "ImplRef", "IsAbstract")
    SELECT v_class, m[2], m[4], 'sql', m[3], false
    WHERE NOT EXISTS (SELECT 1 FROM "Meta"."Methods" x WHERE x."ClassID" = v_class AND x."Name" = m[2]);
    UPDATE "Meta"."Methods" x
    SET "Description" = m[4], "ImplKind" = 'sql', "ImplRef" = m[3], "IsAbstract" = false
    WHERE x."ClassID" = v_class AND x."Name" = m[2]
      AND (x."Description", x."ImplKind", x."ImplRef", x."IsAbstract") IS DISTINCT FROM (m[4], 'sql', m[3], false);
  END LOOP;
END
$methods$;

-- Engine hook ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION omics_clinical.trg_node_execution_meta_objects()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, omics_clinical, pg_temp
AS $$
DECLARE
  v_class text;
BEGIN
  SELECT s.class_name INTO v_class
  FROM wf.workflow_node wn
  JOIN wf.workflow_action wa ON wa.id = wn.workflow_action_id
  JOIN omics_clinical.wf_meta_object_source s ON s.action_name = wa.action_name
  WHERE wn.id = NEW.workflow_node_id;
  IF v_class IS NULL THEN
    RETURN NULL;
  END IF;
  BEGIN
    IF v_class = 'omics/SequencingRun' THEN
      PERFORM omics_clinical.sp_sequencing_run_record(NEW.id);
    ELSE
      PERFORM omics_clinical.sp_alignment_result_record(NEW.id);
    END IF;
  EXCEPTION WHEN OTHERS THEN
    -- Never fail the engine's transaction; the row stays in v_run_alignment_pending.
    RAISE WARNING 'omics: % not recorded for node execution %: %', v_class, NEW.id, SQLERRM;
  END;
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_omics_node_execution_meta_objects ON wf.node_execution;
CREATE TRIGGER trg_omics_node_execution_meta_objects
  AFTER INSERT OR UPDATE OF status, output_json ON wf.node_execution
  FOR EACH ROW
  WHEN (NEW.status = 'SUCCEEDED')
  EXECUTE FUNCTION omics_clinical.trg_node_execution_meta_objects();
