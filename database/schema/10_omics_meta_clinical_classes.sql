-- GoliathOmics R4 — Meta-backed clinical classes, legacy MetaObjectID bridge, cutover flags.
-- Prerequisites: GoliathApp Meta phases + omics_clinical semantic MVP (08/09).
SET client_min_messages = warning;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS omics_clinical;

CREATE TABLE IF NOT EXISTS omics_clinical.meta_cutover_flag (
  entity_code text PRIMARY KEY,
  dual_write_legacy_meta boolean NOT NULL DEFAULT false,
  read_from_meta boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

INSERT INTO omics_clinical.meta_cutover_flag (entity_code)
VALUES
  ('customer'),
  ('disease'),
  ('patient'),
  ('sample'),
  ('lab_batch')
ON CONFLICT (entity_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS omics_clinical.meta_class_registry (
  entity_code text PRIMARY KEY,
  class_id integer NOT NULL REFERENCES "Meta"."Classes" ("ID"),
  class_name text NOT NULL,
  semantic_entity_id integer NULL
);

CREATE OR REPLACE FUNCTION omics_clinical.fn_meta_cutover_flag(p_entity_code text)
RETURNS omics_clinical.meta_cutover_flag
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, pg_temp
AS $$
  SELECT f.*
  FROM omics_clinical.meta_cutover_flag f
  WHERE f.entity_code = p_entity_code;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_meta_dual_write_enabled(p_entity_code text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, pg_temp
AS $$
  SELECT COALESCE(
    (SELECT dual_write_legacy_meta FROM omics_clinical.meta_cutover_flag WHERE entity_code = p_entity_code),
    false
  );
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_meta_read_from_meta(p_entity_code text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, pg_temp
AS $$
  SELECT COALESCE(
    (SELECT read_from_meta FROM omics_clinical.meta_cutover_flag WHERE entity_code = p_entity_code),
    false
  );
$$;

ALTER TABLE portal."Customers"
  ADD COLUMN IF NOT EXISTS "MetaObjectID" integer NULL;

ALTER TABLE portal."Diseases"
  ADD COLUMN IF NOT EXISTS "MetaObjectID" integer NULL;

ALTER TABLE portal."Patients"
  ADD COLUMN IF NOT EXISTS "MetaObjectID" integer NULL;

ALTER TABLE portal."Samples"
  ADD COLUMN IF NOT EXISTS "MetaObjectID" integer NULL;

ALTER TABLE portal."Batches"
  ADD COLUMN IF NOT EXISTS "MetaObjectID" integer NULL;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_portal_customers_meta_object') THEN
    ALTER TABLE portal."Customers"
      ADD CONSTRAINT fk_portal_customers_meta_object
      FOREIGN KEY ("MetaObjectID") REFERENCES "Meta"."Objs" ("ID");
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_portal_diseases_meta_object') THEN
    ALTER TABLE portal."Diseases"
      ADD CONSTRAINT fk_portal_diseases_meta_object
      FOREIGN KEY ("MetaObjectID") REFERENCES "Meta"."Objs" ("ID");
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_portal_patients_meta_object') THEN
    ALTER TABLE portal."Patients"
      ADD CONSTRAINT fk_portal_patients_meta_object
      FOREIGN KEY ("MetaObjectID") REFERENCES "Meta"."Objs" ("ID");
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_portal_samples_meta_object') THEN
    ALTER TABLE portal."Samples"
      ADD CONSTRAINT fk_portal_samples_meta_object
      FOREIGN KEY ("MetaObjectID") REFERENCES "Meta"."Objs" ("ID");
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_portal_batches_meta_object') THEN
    ALTER TABLE portal."Batches"
      ADD CONSTRAINT fk_portal_batches_meta_object
      FOREIGN KEY ("MetaObjectID") REFERENCES "Meta"."Objs" ("ID");
  END IF;
EXCEPTION
  WHEN undefined_table THEN
    RAISE NOTICE 'skip meta object FKs: Meta.Objs not present yet';
END
$fk$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_meta_ensure_wf_type(
  p_name text,
  p_kind text,
  p_schema_json jsonb
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = wf, pg_temp
AS $$
DECLARE
  v_id bigint;
BEGIN
  INSERT INTO wf.data_type AS dt (name, version, status, kind, schema_json, content_hash)
  VALUES (
    p_name,
    '1',
    'published',
    p_kind,
    p_schema_json,
    md5(p_name || '/1/' || p_schema_json::text)
  )
  ON CONFLICT ON CONSTRAINT uq_wf_data_type_name_version DO UPDATE SET
    kind = EXCLUDED.kind,
    schema_json = EXCLUDED.schema_json,
    content_hash = EXCLUDED.content_hash
  RETURNING dt.id INTO v_id;
  RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_upsert_class_field(
  p_class_id integer,
  p_field_name text,
  p_type_name text,
  p_type_kind text,
  p_type_schema jsonb,
  p_required boolean DEFAULT false
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = "Meta", omics_clinical, wf, pg_temp
AS $$
DECLARE
  v_type_id bigint;
BEGIN
  v_type_id := omics_clinical.fn_meta_ensure_wf_type(p_type_name, p_type_kind, p_type_schema);
  INSERT INTO "Meta"."Fields" ("ClassID", "Name", "TypeID", "Required")
  VALUES (p_class_id, p_field_name, v_type_id, p_required)
  ON CONFLICT ("ClassID", "Name") DO UPDATE SET
    "TypeID" = EXCLUDED."TypeID",
    "Required" = EXCLUDED."Required";
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_register_clinical_class(
  p_entity_code text,
  p_class_name text,
  p_display_name text,
  p_description text,
  p_fields jsonb
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = "Meta", omics_clinical, portal, wf, pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_field jsonb;
  v_entity_id integer;
BEGIN
  SELECT r.class_id INTO v_class_id
  FROM omics_clinical.meta_class_registry r
  WHERE r.entity_code = p_entity_code;

  IF v_class_id IS NULL THEN
    SELECT c."ID" INTO v_class_id
    FROM "Meta"."Classes" c
    WHERE c."Name" = p_class_name;

    IF v_class_id IS NULL THEN
      INSERT INTO "Meta"."Classes" ("Name")
      VALUES (p_class_name)
      RETURNING "ID" INTO v_class_id;
    END IF;

    INSERT INTO omics_clinical.meta_class_registry (entity_code, class_id, class_name)
    VALUES (p_entity_code, v_class_id, p_class_name)
    ON CONFLICT (entity_code) DO UPDATE SET
      class_id = EXCLUDED.class_id,
      class_name = EXCLUDED.class_name;
  END IF;

  FOR v_field IN SELECT * FROM jsonb_array_elements(p_fields)
  LOOP
    PERFORM omics_clinical.sp_meta_upsert_class_field(
      v_class_id,
      v_field->>'name',
      v_field->>'type_name',
      v_field->>'type_kind',
      (v_field->'type_schema')::jsonb,
      COALESCE((v_field->>'required')::boolean, false)
    );
  END LOOP;

  v_entity_id := "Meta".sp_semantic_sync_class_entity(
    v_class_id,
    'omics',
    p_entity_code,
    p_display_name,
    p_description
  );

  UPDATE omics_clinical.meta_class_registry
  SET semantic_entity_id = v_entity_id
  WHERE entity_code = p_entity_code;

  RETURN v_class_id;
END;
$$;
