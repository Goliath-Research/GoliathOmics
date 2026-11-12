-- GoliathOmics — mirror cfg.study rows into omics/Study Meta objects (workbench visibility).
--
-- cfg.study + cfg.study_instance_link (GoliathApp) hold the study definition and its runs.
-- The workbench lists Meta objects through portal."ScopedMetaObjects", so a study is only
-- visible there once it has an omics/Study object in a scope. This file adds:
--   * study fields on omics/Study (15_* registers the class; platform/Activity gives Label),
--   * omics_clinical.study_meta_link (cfg.study row -> Meta object, one per study),
--   * omics_clinical.sp_meta_mirror_study(study_row_id, scope_id): idempotent create/update.
-- Requires 10_* (omics_clinical.sp_meta_upsert_class_field) and 15_* (omics/Study).

DO $study_fields$
DECLARE
  v_class_id integer;
BEGIN
  SELECT c."ID" INTO v_class_id FROM "Meta"."Classes" c WHERE c."Name" = 'omics/Study';
  IF v_class_id IS NULL THEN
    RAISE NOTICE 'omics/Study class is missing; skipping study fields';
    RETURN;
  END IF;

  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'name', 'omics/meta/string', 'string', '{"type": "string"}'::jsonb, false);
  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'description', 'omics/meta/string_nullable', 'string',
    '{"type": ["string", "null"]}'::jsonb, false);
  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'cfg_study_row_id', 'omics/meta/int', 'int', '{"type": "integer"}'::jsonb, false);
  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'study_version', 'omics/meta/string', 'string', '{"type": "string"}'::jsonb, false);
  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'status', 'omics/meta/string', 'string', '{"type": "string"}'::jsonb, false);
  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'run_count', 'omics/meta/int', 'int', '{"type": "integer"}'::jsonb, false);
  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'first_run_started_at_utc', 'omics/meta/string_nullable', 'string',
    '{"type": ["string", "null"]}'::jsonb, false);
  PERFORM omics_clinical.sp_meta_upsert_class_field(
    v_class_id, 'last_run_completed_at_utc', 'omics/meta/string_nullable', 'string',
    '{"type": ["string", "null"]}'::jsonb, false);

  PERFORM "Meta".sp_class_regenerate_views(v_class_id);
END
$study_fields$;

CREATE TABLE IF NOT EXISTS omics_clinical.study_meta_link (
  study_row_id bigint NOT NULL,
  meta_obj_id integer NOT NULL,
  scope_id integer NOT NULL,
  created_at_utc timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  updated_at_utc timestamptz NULL,
  CONSTRAINT pk_omics_study_meta_link PRIMARY KEY (study_row_id),
  CONSTRAINT uq_omics_study_meta_link_obj UNIQUE (meta_obj_id),
  CONSTRAINT fk_omics_study_meta_link_study FOREIGN KEY (study_row_id)
    REFERENCES cfg.study (id) ON DELETE CASCADE,
  CONSTRAINT fk_omics_study_meta_link_obj FOREIGN KEY (meta_obj_id)
    REFERENCES "Meta"."Objs" ("ID") ON DELETE CASCADE
);

-- Create or refresh the omics/Study object for one cfg.study row and make sure it is
-- scoped (portal."ScopedMetaObjects"). Returns the Meta object id. Safe to rerun: the
-- object is updated in place, its scope row is kept, and no duplicate is created.
CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_mirror_study(
  p_study_row_id bigint,
  p_scope_id integer
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, "Meta", omics_clinical, portal, cfg, wf, pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_study cfg.study%ROWTYPE;
  v_obj_id integer;
  v_scoped_to integer;
  v_doc jsonb;
  v_runs integer;
  v_first timestamptz;
  v_last timestamptz;
BEGIN
  IF p_study_row_id IS NULL OR p_study_row_id <= 0 THEN
    RAISE EXCEPTION 'study_row_id is required';
  END IF;
  IF p_scope_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM "RBAC"."Scopes" s WHERE s."ScopeID" = p_scope_id
  ) THEN
    RAISE EXCEPTION 'scope % not found', p_scope_id;
  END IF;

  SELECT c."ID" INTO v_class_id FROM "Meta"."Classes" c WHERE c."Name" = 'omics/Study';
  IF v_class_id IS NULL THEN
    RAISE EXCEPTION 'omics/Study Meta class is missing (apply 15_omics_meta_domain_classes.sql)';
  END IF;

  SELECT * INTO v_study FROM cfg.study s WHERE s.id = p_study_row_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'cfg.study % not found', p_study_row_id;
  END IF;

  SELECT count(*)::integer, min(i.started_at_utc), max(i.completed_at_utc)
  INTO v_runs, v_first, v_last
  FROM cfg.study_instance_link sil
  JOIN wf.workflow_instance i ON i.id = sil.workflow_instance_id
  WHERE sil.study_row_id = p_study_row_id;

  v_doc := jsonb_build_object(
    'Label', v_study.name,
    'name', v_study.name,
    'description', NULLIF(btrim(COALESCE(v_study.document_json->>'description', '')), ''),
    'cfg_study_row_id', v_study.id,
    'study_version', v_study.version,
    'status', v_study.status,
    'run_count', COALESCE(v_runs, 0),
    'first_run_started_at_utc',
      to_char(v_first AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'last_run_completed_at_utc',
      to_char(v_last AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
  );

  SELECT l.meta_obj_id INTO v_obj_id
  FROM omics_clinical.study_meta_link l
  WHERE l.study_row_id = p_study_row_id
  FOR UPDATE;

  IF v_obj_id IS NULL THEN
    v_obj_id := "Meta".sp_obj_insert(v_class_id, v_doc);
    INSERT INTO omics_clinical.study_meta_link (study_row_id, meta_obj_id, scope_id)
    VALUES (p_study_row_id, v_obj_id, p_scope_id);
  ELSE
    PERFORM "Meta".sp_obj_update(v_obj_id, v_doc);
    UPDATE omics_clinical.study_meta_link
    SET updated_at_utc = (now() AT TIME ZONE 'utc')
    WHERE study_row_id = p_study_row_id;
  END IF;

  SELECT sm."ScopeID" INTO v_scoped_to
  FROM portal."ScopedMetaObjects" sm
  WHERE sm."ObjID" = v_obj_id;

  IF v_scoped_to IS NULL THEN
    INSERT INTO portal."ScopedMetaObjects" ("ObjID", "ScopeID")
    VALUES (v_obj_id, p_scope_id);
  ELSIF v_scoped_to <> p_scope_id THEN
    RAISE EXCEPTION 'omics/Study object % is already scoped to scope %, not %',
      v_obj_id, v_scoped_to, p_scope_id;
  END IF;

  RETURN v_obj_id;
END;
$$;

REVOKE ALL ON FUNCTION omics_clinical.sp_meta_mirror_study(bigint, integer) FROM PUBLIC;
