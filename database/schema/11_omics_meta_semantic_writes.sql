-- GoliathOmics R4 PR2 — semantic write requests, dual-write to Meta, read-path cutover flags.
SET client_min_messages = warning;

CREATE OR REPLACE PROCEDURE omics_clinical.sp_meta_set_cutover_flag(
  p_entity_code text,
  p_dual_write_legacy_meta boolean,
  p_read_from_meta boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, pg_temp
AS $$
BEGIN
  UPDATE omics_clinical.meta_cutover_flag
  SET dual_write_legacy_meta = p_dual_write_legacy_meta,
      read_from_meta = p_read_from_meta,
      updated_at = (now() AT TIME ZONE 'utc')
  WHERE entity_code = p_entity_code;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown entity_code %', p_entity_code;
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_meta_registry_class_id(p_entity_code text)
RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, pg_temp
AS $$
  SELECT class_id FROM omics_clinical.meta_class_registry WHERE entity_code = p_entity_code;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_mirror_disease(p_disease_id integer)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, "Meta", pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_row portal."Diseases"%ROWTYPE;
  v_payload jsonb;
  v_obj_id integer;
BEGIN
  v_class_id := omics_clinical.fn_meta_registry_class_id('disease');
  SELECT * INTO v_row FROM portal."Diseases" WHERE "ID" = p_disease_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'disease % not found', p_disease_id;
  END IF;
  v_payload := jsonb_build_object(
    'legacy_disease_id', v_row."ID",
    'name', v_row."Name",
    'parent_id', v_row."ParentID"
  );
  IF v_row."MetaObjectID" IS NULL THEN
    v_obj_id := "Meta".sp_obj_insert(v_class_id, v_payload);
    UPDATE portal."Diseases" SET "MetaObjectID" = v_obj_id WHERE "ID" = p_disease_id;
  ELSE
    PERFORM "Meta".sp_obj_update(v_row."MetaObjectID", v_payload);
    v_obj_id := v_row."MetaObjectID";
  END IF;
  RETURN v_obj_id;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_mirror_customer(p_customer_id integer)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, "Meta", pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_row portal."Customers"%ROWTYPE;
  v_payload jsonb;
  v_obj_id integer;
BEGIN
  v_class_id := omics_clinical.fn_meta_registry_class_id('customer');
  SELECT * INTO v_row FROM portal."Customers" WHERE "ID" = p_customer_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'customer % not found', p_customer_id;
  END IF;
  v_payload := jsonb_build_object(
    'legacy_customer_id', v_row."ID",
    'name', v_row."Name",
    'organization_id', v_row."OrganizationID"
  );
  IF v_row."MetaObjectID" IS NULL THEN
    v_obj_id := "Meta".sp_obj_insert(v_class_id, v_payload);
    UPDATE portal."Customers" SET "MetaObjectID" = v_obj_id WHERE "ID" = p_customer_id;
  ELSE
    PERFORM "Meta".sp_obj_update(v_row."MetaObjectID", v_payload);
    v_obj_id := v_row."MetaObjectID";
  END IF;
  RETURN v_obj_id;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_mirror_patient(p_patient_id integer)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, "Meta", pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_row portal."Patients"%ROWTYPE;
  v_payload jsonb;
  v_obj_id integer;
BEGIN
  v_class_id := omics_clinical.fn_meta_registry_class_id('patient');
  SELECT * INTO v_row FROM portal."Patients" WHERE "ID" = p_patient_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'patient % not found', p_patient_id;
  END IF;
  v_payload := jsonb_build_object(
    'legacy_patient_id', v_row."ID",
    'institution_id', v_row."InstitutionID",
    'name', v_row."Name",
    'sex', v_row."Sex",
    'year_born', v_row."YearBorn",
    'race', v_row."Race",
    'ethnicity', v_row."Ethnicity",
    'external_id', v_row."ExternalID"
  );
  IF v_row."MetaObjectID" IS NULL THEN
    v_obj_id := "Meta".sp_obj_insert(v_class_id, v_payload);
    UPDATE portal."Patients" SET "MetaObjectID" = v_obj_id WHERE "ID" = p_patient_id;
  ELSE
    PERFORM "Meta".sp_obj_update(v_row."MetaObjectID", v_payload);
    v_obj_id := v_row."MetaObjectID";
  END IF;
  RETURN v_obj_id;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_mirror_sample(p_sample_id integer)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, "Meta", pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_row portal."Samples"%ROWTYPE;
  v_payload jsonb;
  v_obj_id integer;
BEGIN
  v_class_id := omics_clinical.fn_meta_registry_class_id('sample');
  SELECT * INTO v_row FROM portal."Samples" WHERE "ID" = p_sample_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'sample % not found', p_sample_id;
  END IF;
  v_payload := jsonb_build_object(
    'legacy_sample_id', v_row."ID",
    'patient_id', v_row."PatientID",
    'customer_id', v_row."CustomerID",
    'disease_id', v_row."DiseaseID",
    'sample_name', v_row."SampleName",
    'age', v_row."Age",
    'bmi', v_row."BMI",
    'stage', v_row."Stage"
  );
  IF v_row."MetaObjectID" IS NULL THEN
    v_obj_id := "Meta".sp_obj_insert(v_class_id, v_payload);
    UPDATE portal."Samples" SET "MetaObjectID" = v_obj_id WHERE "ID" = p_sample_id;
  ELSE
    PERFORM "Meta".sp_obj_update(v_row."MetaObjectID", v_payload);
    v_obj_id := v_row."MetaObjectID";
  END IF;
  RETURN v_obj_id;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.sp_meta_mirror_lab_batch(p_batch_id integer)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, "Meta", pg_temp
AS $$
DECLARE
  v_class_id integer;
  v_row portal."Batches"%ROWTYPE;
  v_payload jsonb;
  v_obj_id integer;
BEGIN
  v_class_id := omics_clinical.fn_meta_registry_class_id('lab_batch');
  SELECT * INTO v_row FROM portal."Batches" WHERE "ID" = p_batch_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'batch % not found', p_batch_id;
  END IF;
  v_payload := jsonb_build_object(
    'legacy_batch_id', v_row."ID",
    'name', v_row."Name",
    'lab_id', v_row."LabID"
  );
  IF v_row."MetaObjectID" IS NULL THEN
    v_obj_id := "Meta".sp_obj_insert(v_class_id, v_payload);
    UPDATE portal."Batches" SET "MetaObjectID" = v_obj_id WHERE "ID" = p_batch_id;
  ELSE
    PERFORM "Meta".sp_obj_update(v_row."MetaObjectID", v_payload);
    v_obj_id := v_row."MetaObjectID";
  END IF;
  RETURN v_obj_id;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_disease_create(
  p_user_id integer,
  p_payload jsonb
)
RETURNS portal.omics_disease_row
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_id integer;
  v_row portal.omics_disease_row;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  INSERT INTO portal."Diseases" ("Name", "ParentID")
  VALUES (
    NULLIF(btrim(p_payload->>'name'), ''),
    NULLIF(p_payload->>'parent_id', '')::integer
  )
  RETURNING "ID" INTO v_id;
  IF omics_clinical.fn_meta_dual_write_enabled('disease') THEN
    PERFORM omics_clinical.sp_meta_mirror_disease(v_id);
  END IF;
  SELECT v.disease_id, v.name, v.parent_id
  INTO v_row
  FROM omics_clinical.v_disease v
  WHERE v.disease_id = v_id;
  RETURN v_row;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_disease_update(
  p_user_id integer,
  p_payload jsonb
)
RETURNS portal.omics_disease_row
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_id integer;
  v_row portal.omics_disease_row;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  v_id := NULLIF(p_payload->>'disease_id', '')::integer;
  IF v_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS400', 'disease_id required');
  END IF;
  UPDATE portal."Diseases" d
  SET "Name" = COALESCE(NULLIF(btrim(p_payload->>'name'), ''), d."Name"),
      "ParentID" = COALESCE(NULLIF(p_payload->>'parent_id', '')::integer, d."ParentID")
  WHERE d."ID" = v_id;
  IF NOT FOUND THEN
    PERFORM portal.fn_semantic_raise('GS404', 'disease not found');
  END IF;
  IF omics_clinical.fn_meta_dual_write_enabled('disease') THEN
    PERFORM omics_clinical.sp_meta_mirror_disease(v_id);
  END IF;
  SELECT v.disease_id, v.name, v.parent_id
  INTO v_row
  FROM omics_clinical.v_disease v
  WHERE v.disease_id = v_id;
  RETURN v_row;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_customer_create(
  p_user_id integer,
  p_payload jsonb
)
RETURNS portal.omics_customer_row
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_org_id integer;
  v_id integer;
  v_row portal.omics_customer_row;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  v_org_id := NULLIF(p_payload->>'organization_id', '')::integer;
  IF v_org_id IS NULL OR NOT omics_clinical.fn_user_can_access_organization(p_user_id, v_org_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'organization scope denied');
  END IF;
  INSERT INTO portal."Customers" ("Name", "OrganizationID")
  VALUES (NULLIF(btrim(p_payload->>'name'), ''), v_org_id)
  RETURNING "ID" INTO v_id;
  IF omics_clinical.fn_meta_dual_write_enabled('customer') THEN
    PERFORM omics_clinical.sp_meta_mirror_customer(v_id);
  END IF;
  SELECT v.customer_id, v.name, v.organization_id
  INTO v_row
  FROM omics_clinical.v_customer v
  WHERE v.customer_id = v_id;
  RETURN v_row;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_sample_create(
  p_user_id integer,
  p_payload jsonb
)
RETURNS portal.omics_sample_row
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_customer_id integer;
  v_id integer;
  v_row portal.omics_sample_row;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  v_customer_id := NULLIF(p_payload->>'customer_id', '')::integer;
  IF v_customer_id IS NOT NULL
    AND NOT omics_clinical.fn_user_can_access_customer(p_user_id, v_customer_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'customer scope denied');
  END IF;
  INSERT INTO portal."Samples" (
    "PatientID", "CustomerID", "DiseaseID", "SampleName", "Age", "BMI", "Stage"
  )
  VALUES (
    NULLIF(p_payload->>'patient_id', '')::integer,
    v_customer_id,
    NULLIF(p_payload->>'disease_id', '')::integer,
    NULLIF(p_payload->>'sample_name', ''),
    NULLIF(p_payload->>'age', '')::integer,
    NULLIF(p_payload->>'bmi', '')::double precision,
    NULLIF(p_payload->>'stage', '')
  )
  RETURNING "ID" INTO v_id;
  IF omics_clinical.fn_meta_dual_write_enabled('sample') THEN
    PERFORM omics_clinical.sp_meta_mirror_sample(v_id);
  END IF;
  SELECT v.sample_id, v.patient_id, v.customer_id, v.disease_id, v.sample_name, v.age, v.bmi
  INTO v_row
  FROM omics_clinical.v_sample v
  WHERE v.sample_id = v_id;
  RETURN v_row;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_group_add_sample(
  p_user_id integer,
  p_payload jsonb
)
RETURNS portal.omics_group_row
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_group_id integer;
  v_sample_id integer;
  v_customer_id integer;
  v_row portal.omics_group_row;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  v_group_id := NULLIF(p_payload->>'group_id', '')::integer;
  v_sample_id := NULLIF(p_payload->>'sample_id', '')::integer;
  SELECT g."CustomerID" INTO v_customer_id FROM portal."Groups" g WHERE g."ID" = v_group_id;
  IF v_customer_id IS NOT NULL
    AND NOT omics_clinical.fn_user_can_access_customer(p_user_id, v_customer_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'group scope denied');
  END IF;
  INSERT INTO portal."GroupSamples" ("GroupID", "SampleID")
  VALUES (v_group_id, v_sample_id)
  ON CONFLICT DO NOTHING;
  SELECT v.group_id, v.name, v.description, v.customer_id, v.sample_count
  INTO v_row
  FROM omics_clinical.v_group v
  WHERE v.group_id = v_group_id;
  RETURN v_row;
END;
$$;

CREATE OR REPLACE VIEW omics_clinical.v_disease_read AS
SELECT
  COALESCE((m.legacy_disease_id)::integer, d."ID") AS disease_id,
  COALESCE(m.name::text, d."Name"::text) AS name,
  COALESCE(m.parent_id::integer, d."ParentID") AS parent_id
FROM portal."Diseases" d
LEFT JOIN "Meta"."VOmicsDisease" m ON m."ID" = d."MetaObjectID";

CREATE OR REPLACE VIEW omics_clinical.v_customer_read AS
SELECT
  COALESCE((m.legacy_customer_id)::integer, c."ID") AS customer_id,
  COALESCE(m.name::text, c."Name"::text) AS name,
  COALESCE(m.organization_id::integer, c."OrganizationID") AS organization_id
FROM portal."Customers" c
LEFT JOIN "Meta"."VOmicsCustomer" m ON m."ID" = c."MetaObjectID";

CREATE OR REPLACE VIEW omics_clinical.v_sample_read AS
SELECT
  COALESCE((m.legacy_sample_id)::integer, s."ID") AS sample_id,
  COALESCE(m.patient_id::integer, s."PatientID") AS patient_id,
  COALESCE(m.customer_id::integer, s."CustomerID") AS customer_id,
  COALESCE(m.disease_id::integer, s."DiseaseID") AS disease_id,
  COALESCE(m.sample_name::text, s."SampleName"::text) AS sample_name,
  COALESCE(m.age::numeric, s."Age"::numeric) AS age,
  COALESCE(m.bmi::double precision, s."BMI") AS bmi
FROM portal."Samples" s
LEFT JOIN "Meta"."VOmicsSample" m ON m."ID" = s."MetaObjectID";

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_disease_list(
  p_user_id integer,
  p_params jsonb,
  p_limit integer
)
RETURNS TABLE (
  disease_id integer,
  name text,
  parent_id integer
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_parent_id integer;
  v_lim integer;
BEGIN
  IF p_user_id IS NULL THEN
    RETURN;
  END IF;
  v_parent_id := NULLIF(p_params->>'parent_id', '')::integer;
  v_lim := GREATEST(0, COALESCE(p_limit, 500));
  IF omics_clinical.fn_meta_read_from_meta('disease') THEN
    RETURN QUERY
    SELECT v.disease_id, v.name, v.parent_id
    FROM omics_clinical.v_disease_read v
    WHERE (v_parent_id IS NULL OR v.parent_id = v_parent_id)
    ORDER BY v.disease_id
    LIMIT v_lim;
  ELSE
    RETURN QUERY
    SELECT v.disease_id, v.name, v.parent_id
    FROM omics_clinical.v_disease v
    WHERE (v_parent_id IS NULL OR v.parent_id = v_parent_id)
    ORDER BY v.disease_id
    LIMIT v_lim;
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_customer_list(
  p_user_id integer,
  p_params jsonb,
  p_limit integer
)
RETURNS TABLE (
  customer_id integer,
  name text,
  organization_id integer
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_org_id integer;
  v_lim integer;
BEGIN
  IF p_user_id IS NULL THEN
    RETURN;
  END IF;
  v_org_id := NULLIF(p_params->>'organization_id', '')::integer;
  IF v_org_id IS NOT NULL
    AND NOT omics_clinical.fn_user_can_access_organization(p_user_id, v_org_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'organization scope denied');
  END IF;
  v_lim := GREATEST(0, COALESCE(p_limit, 500));
  IF omics_clinical.fn_meta_read_from_meta('customer') THEN
    RETURN QUERY
    SELECT v.customer_id, v.name, v.organization_id
    FROM omics_clinical.v_customer_read v
    WHERE v.organization_id = ANY (portal.fn_semantic_user_organization_ids(p_user_id))
      AND (v_org_id IS NULL OR v.organization_id = v_org_id)
    ORDER BY v.name
    LIMIT v_lim;
  ELSE
    RETURN QUERY
    SELECT v.customer_id, v.name, v.organization_id
    FROM omics_clinical.v_customer v
    WHERE v.organization_id = ANY (portal.fn_semantic_user_organization_ids(p_user_id))
      AND (v_org_id IS NULL OR v.organization_id = v_org_id)
    ORDER BY v.name
    LIMIT v_lim;
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_sample_list(
  p_user_id integer,
  p_params jsonb,
  p_limit integer
)
RETURNS TABLE (
  sample_id integer,
  patient_id integer,
  customer_id integer,
  disease_id integer,
  sample_name text,
  age numeric,
  bmi double precision
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_customer_id integer;
  v_disease_id integer;
  v_group_id integer;
  v_lim integer;
BEGIN
  IF p_user_id IS NULL THEN
    RETURN;
  END IF;
  v_customer_id := NULLIF(p_params->>'customer_id', '')::integer;
  v_disease_id := NULLIF(p_params->>'disease_id', '')::integer;
  v_group_id := NULLIF(p_params->>'group_id', '')::integer;
  IF v_customer_id IS NOT NULL
    AND NOT omics_clinical.fn_user_can_access_customer(p_user_id, v_customer_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'customer scope denied');
  END IF;
  v_lim := GREATEST(0, COALESCE(p_limit, 500));
  IF omics_clinical.fn_meta_read_from_meta('sample') THEN
    RETURN QUERY
    SELECT v.sample_id, v.patient_id, v.customer_id, v.disease_id, v.sample_name, v.age, v.bmi
    FROM omics_clinical.v_sample_read v
    LEFT JOIN portal."GroupSamples" gs ON gs."SampleID" = v.sample_id
    WHERE (v_customer_id IS NULL OR v.customer_id = v_customer_id)
      AND (v_disease_id IS NULL OR v.disease_id = v_disease_id)
      AND (v_group_id IS NULL OR gs."GroupID" = v_group_id)
      AND (
        v.customer_id IS NULL
        OR omics_clinical.fn_user_can_access_customer(p_user_id, v.customer_id)
      )
    ORDER BY v.sample_id
    LIMIT v_lim;
  ELSE
    RETURN QUERY
    SELECT v.sample_id, v.patient_id, v.customer_id, v.disease_id, v.sample_name, v.age, v.bmi
    FROM omics_clinical.v_sample v
    LEFT JOIN portal."GroupSamples" gs ON gs."SampleID" = v.sample_id
    WHERE (v_customer_id IS NULL OR v.customer_id = v_customer_id)
      AND (v_disease_id IS NULL OR v.disease_id = v_disease_id)
      AND (v_group_id IS NULL OR gs."GroupID" = v_group_id)
      AND (
        v.customer_id IS NULL
        OR omics_clinical.fn_user_can_access_customer(p_user_id, v.customer_id)
      )
    ORDER BY v.sample_id
    LIMIT v_lim;
  END IF;
END;
$$;
