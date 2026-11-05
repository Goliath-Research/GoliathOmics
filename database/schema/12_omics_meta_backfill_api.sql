-- GoliathOmics R4 PR3 — one-time backfill from legacy portal rows to Meta ObjValues.
-- Run manually per entity after PR1/PR2 are deployed (see scripts/omics_meta_backfill.sh).
SET client_min_messages = warning;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION omics_clinical.fn_meta_backfill_count(p_entity_code text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_count bigint;
BEGIN
  CASE p_entity_code
    WHEN 'disease' THEN
      SELECT count(*) INTO v_count FROM portal."Diseases" WHERE "MetaObjectID" IS NULL;
    WHEN 'customer' THEN
      SELECT count(*) INTO v_count FROM portal."Customers" WHERE "MetaObjectID" IS NULL;
    WHEN 'patient' THEN
      SELECT count(*) INTO v_count FROM portal."Patients" WHERE "MetaObjectID" IS NULL;
    WHEN 'sample' THEN
      SELECT count(*) INTO v_count FROM portal."Samples" WHERE "MetaObjectID" IS NULL;
    WHEN 'lab_batch' THEN
      SELECT count(*) INTO v_count FROM portal."Batches" WHERE "MetaObjectID" IS NULL;
    ELSE
      RAISE EXCEPTION 'unknown entity_code %', p_entity_code;
  END CASE;
  RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_meta_legacy_checksum(p_entity_code text)
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_payload text;
BEGIN
  CASE p_entity_code
    WHEN 'disease' THEN
      SELECT md5(string_agg(d."ID"::text || '|' || d."Name" || '|' || coalesce(d."ParentID"::text, ''), E'\n' ORDER BY d."ID"))
      INTO v_payload FROM portal."Diseases" d;
    WHEN 'customer' THEN
      SELECT md5(string_agg(c."ID"::text || '|' || c."Name" || '|' || c."OrganizationID"::text, E'\n' ORDER BY c."ID"))
      INTO v_payload FROM portal."Customers" c;
    WHEN 'patient' THEN
      SELECT md5(string_agg(p."ID"::text || '|' || p."Name", E'\n' ORDER BY p."ID"))
      INTO v_payload FROM portal."Patients" p;
    WHEN 'sample' THEN
      SELECT md5(string_agg(s."ID"::text || '|' || coalesce(s."SampleName", ''), E'\n' ORDER BY s."ID"))
      INTO v_payload FROM portal."Samples" s;
    WHEN 'lab_batch' THEN
      SELECT md5(string_agg(b."ID"::text || '|' || coalesce(b."Name", ''), E'\n' ORDER BY b."ID"))
      INTO v_payload FROM portal."Batches" b;
    ELSE
      RAISE EXCEPTION 'unknown entity_code %', p_entity_code;
  END CASE;
  RETURN coalesce(v_payload, md5(''));
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_backfill_entity(p_entity_code text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, "Meta", pg_temp
AS $$
DECLARE
  v_id integer;
  v_mirrored bigint := 0;
BEGIN
  CASE p_entity_code
    WHEN 'disease' THEN
      FOR v_id IN SELECT d."ID" FROM portal."Diseases" d WHERE d."MetaObjectID" IS NULL ORDER BY d."ID"
      LOOP
        PERFORM omics_clinical.sp_meta_mirror_disease(v_id);
        v_mirrored := v_mirrored + 1;
      END LOOP;
    WHEN 'customer' THEN
      FOR v_id IN SELECT c."ID" FROM portal."Customers" c WHERE c."MetaObjectID" IS NULL ORDER BY c."ID"
      LOOP
        PERFORM omics_clinical.sp_meta_mirror_customer(v_id);
        v_mirrored := v_mirrored + 1;
      END LOOP;
    WHEN 'patient' THEN
      FOR v_id IN SELECT p."ID" FROM portal."Patients" p WHERE p."MetaObjectID" IS NULL ORDER BY p."ID"
      LOOP
        PERFORM omics_clinical.sp_meta_mirror_patient(v_id);
        v_mirrored := v_mirrored + 1;
      END LOOP;
    WHEN 'sample' THEN
      FOR v_id IN SELECT s."ID" FROM portal."Samples" s WHERE s."MetaObjectID" IS NULL ORDER BY s."ID"
      LOOP
        PERFORM omics_clinical.sp_meta_mirror_sample(v_id);
        v_mirrored := v_mirrored + 1;
      END LOOP;
    WHEN 'lab_batch' THEN
      FOR v_id IN SELECT b."ID" FROM portal."Batches" b WHERE b."MetaObjectID" IS NULL ORDER BY b."ID"
      LOOP
        PERFORM omics_clinical.sp_meta_mirror_lab_batch(v_id);
        v_mirrored := v_mirrored + 1;
      END LOOP;
    ELSE
      RAISE EXCEPTION 'unknown entity_code %', p_entity_code;
  END CASE;
  RETURN v_mirrored;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_rollback_entity(p_entity_code text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, "Meta", pg_temp
AS $$
DECLARE
  v_obj_id integer;
  v_cleared bigint := 0;
BEGIN
  CASE p_entity_code
    WHEN 'disease' THEN
      FOR v_obj_id IN SELECT d."MetaObjectID" FROM portal."Diseases" d WHERE d."MetaObjectID" IS NOT NULL
      LOOP
        UPDATE portal."Diseases" SET "MetaObjectID" = NULL WHERE "MetaObjectID" = v_obj_id;
        DELETE FROM "Meta"."Objs" o WHERE o."ID" = v_obj_id;
        v_cleared := v_cleared + 1;
      END LOOP;
    WHEN 'customer' THEN
      FOR v_obj_id IN SELECT c."MetaObjectID" FROM portal."Customers" c WHERE c."MetaObjectID" IS NOT NULL
      LOOP
        UPDATE portal."Customers" SET "MetaObjectID" = NULL WHERE "MetaObjectID" = v_obj_id;
        DELETE FROM "Meta"."Objs" o WHERE o."ID" = v_obj_id;
        v_cleared := v_cleared + 1;
      END LOOP;
    WHEN 'patient' THEN
      FOR v_obj_id IN SELECT p."MetaObjectID" FROM portal."Patients" p WHERE p."MetaObjectID" IS NOT NULL
      LOOP
        UPDATE portal."Patients" SET "MetaObjectID" = NULL WHERE "MetaObjectID" = v_obj_id;
        DELETE FROM "Meta"."Objs" o WHERE o."ID" = v_obj_id;
        v_cleared := v_cleared + 1;
      END LOOP;
    WHEN 'sample' THEN
      FOR v_obj_id IN SELECT s."MetaObjectID" FROM portal."Samples" s WHERE s."MetaObjectID" IS NOT NULL
      LOOP
        UPDATE portal."Samples" SET "MetaObjectID" = NULL WHERE "MetaObjectID" = v_obj_id;
        DELETE FROM "Meta"."Objs" o WHERE o."ID" = v_obj_id;
        v_cleared := v_cleared + 1;
      END LOOP;
    WHEN 'lab_batch' THEN
      FOR v_obj_id IN SELECT b."MetaObjectID" FROM portal."Batches" b WHERE b."MetaObjectID" IS NOT NULL
      LOOP
        UPDATE portal."Batches" SET "MetaObjectID" = NULL WHERE "MetaObjectID" = v_obj_id;
        DELETE FROM "Meta"."Objs" o WHERE o."ID" = v_obj_id;
        v_cleared := v_cleared + 1;
      END LOOP;
    ELSE
      RAISE EXCEPTION 'unknown entity_code %', p_entity_code;
  END CASE;
  UPDATE omics_clinical.meta_cutover_flag
  SET dual_write_legacy_meta = false,
      read_from_meta = false,
      updated_at = (now() AT TIME ZONE 'utc')
  WHERE entity_code = p_entity_code;
  RETURN v_cleared;
END;
$$;
