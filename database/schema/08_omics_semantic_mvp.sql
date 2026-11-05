-- GoliathOmics R3 — read-only clinical semantic list functions (namespace omics).
SET client_min_messages = warning;

CREATE SCHEMA IF NOT EXISTS omics_clinical;

CREATE OR REPLACE FUNCTION omics_clinical.fn_user_can_access_organization(
  p_user_id integer,
  p_organization_id integer
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = portal, pg_temp
AS $$
  SELECT p_user_id IS NOT NULL
    AND p_organization_id IS NOT NULL
    AND p_organization_id = ANY (portal.fn_semantic_user_organization_ids(p_user_id));
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_user_can_access_customer(
  p_user_id integer,
  p_customer_id integer
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = portal, omics_clinical, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM portal."Customers" c
    WHERE c."ID" = p_customer_id
      AND omics_clinical.fn_user_can_access_organization(p_user_id, c."OrganizationID")
  );
$$;

CREATE OR REPLACE VIEW omics_clinical.v_disease AS
SELECT
  d."ID" AS disease_id,
  d."Name"::text AS name,
  d."ParentID" AS parent_id
FROM portal."Diseases" d;

CREATE OR REPLACE VIEW omics_clinical.v_customer AS
SELECT
  c."ID" AS customer_id,
  c."Name"::text AS name,
  c."OrganizationID" AS organization_id
FROM portal."Customers" c;

CREATE OR REPLACE VIEW omics_clinical.v_group AS
SELECT
  g."ID" AS group_id,
  g."Name"::text AS name,
  g."Description"::text AS description,
  g."CustomerID" AS customer_id,
  (
    SELECT count(*)::bigint
    FROM portal."GroupSamples" gs
    WHERE gs."GroupID" = g."ID"
  ) AS sample_count
FROM portal."Groups" g;

CREATE OR REPLACE VIEW omics_clinical.v_sample AS
SELECT
  s."ID" AS sample_id,
  s."PatientID" AS patient_id,
  s."CustomerID" AS customer_id,
  s."DiseaseID" AS disease_id,
  s."SampleName"::text AS sample_name,
  s."Age"::numeric AS age,
  s."BMI" AS bmi
FROM portal."Samples" s;

CREATE OR REPLACE VIEW omics_clinical.v_lab AS
SELECT
  l."ID" AS lab_id,
  l."Name"::text AS name
FROM portal."Labs" l;

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
  RETURN QUERY
  SELECT v.disease_id, v.name, v.parent_id
  FROM omics_clinical.v_disease v
  WHERE (v_parent_id IS NULL OR v.parent_id = v_parent_id)
  ORDER BY v.disease_id
  LIMIT v_lim;
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
  RETURN QUERY
  SELECT v.customer_id, v.name, v.organization_id
  FROM omics_clinical.v_customer v
  WHERE v.organization_id = ANY (portal.fn_semantic_user_organization_ids(p_user_id))
    AND (v_org_id IS NULL OR v.organization_id = v_org_id)
  ORDER BY v.name
  LIMIT v_lim;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_group_list(
  p_user_id integer,
  p_params jsonb,
  p_limit integer
)
RETURNS TABLE (
  group_id integer,
  name text,
  description text,
  customer_id integer,
  sample_count bigint
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_customer_id integer;
  v_disease_id integer;
  v_lim integer;
BEGIN
  IF p_user_id IS NULL THEN
    RETURN;
  END IF;
  v_customer_id := NULLIF(p_params->>'customer_id', '')::integer;
  v_disease_id := NULLIF(p_params->>'disease_id', '')::integer;
  IF v_customer_id IS NOT NULL
    AND NOT omics_clinical.fn_user_can_access_customer(p_user_id, v_customer_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'customer scope denied');
  END IF;
  v_lim := GREATEST(0, COALESCE(p_limit, 500));
  RETURN QUERY
  SELECT v.group_id, v.name, v.description, v.customer_id, v.sample_count
  FROM omics_clinical.v_group v
  WHERE (
    v.customer_id IS NULL
    OR omics_clinical.fn_user_can_access_customer(p_user_id, v.customer_id)
  )
  AND (v_customer_id IS NULL OR v.customer_id = v_customer_id)
  AND (
    v_disease_id IS NULL
    OR EXISTS (
      SELECT 1
      FROM portal."GroupSamples" gs
      INNER JOIN portal."Samples" s ON s."ID" = gs."SampleID"
      WHERE gs."GroupID" = v.group_id
        AND s."DiseaseID" = v_disease_id
    )
  )
  ORDER BY v.name
  LIMIT v_lim;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_group_list_for_enrollment(
  p_user_id integer,
  p_params jsonb,
  p_limit integer
)
RETURNS TABLE (
  group_id integer,
  name text,
  description text,
  customer_id integer,
  sample_count bigint
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_customer_id integer;
  v_lim integer;
BEGIN
  IF p_user_id IS NULL THEN
    RETURN;
  END IF;
  v_customer_id := NULLIF(p_params->>'customer_id', '')::integer;
  IF v_customer_id IS NOT NULL
    AND NOT omics_clinical.fn_user_can_access_customer(p_user_id, v_customer_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'customer scope denied');
  END IF;
  v_lim := GREATEST(0, COALESCE(p_limit, 500));
  RETURN QUERY
  SELECT v.group_id, v.name, v.description, v.customer_id, v.sample_count
  FROM omics_clinical.v_group v
  WHERE (
    v.customer_id IS NULL
    OR omics_clinical.fn_user_can_access_customer(p_user_id, v.customer_id)
  )
  AND (v_customer_id IS NULL OR v.customer_id = v_customer_id)
  ORDER BY v.name
  LIMIT v_lim;
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
  RETURN QUERY
  SELECT
    v.sample_id,
    v.patient_id,
    v.customer_id,
    v.disease_id,
    v.sample_name,
    v.age,
    v.bmi
  FROM omics_clinical.v_sample v
  WHERE (
    v.customer_id IS NULL
    OR omics_clinical.fn_user_can_access_customer(p_user_id, v.customer_id)
  )
  AND (v_customer_id IS NULL OR v.customer_id = v_customer_id)
  AND (v_disease_id IS NULL OR v.disease_id = v_disease_id)
  AND (
    v_group_id IS NULL
    OR EXISTS (
      SELECT 1
      FROM portal."GroupSamples" gs
      WHERE gs."GroupID" = v_group_id
        AND gs."SampleID" = v.sample_id
    )
  )
  ORDER BY v.sample_id
  LIMIT v_lim;
END;
$$;

CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_lab_list(
  p_user_id integer,
  p_params jsonb,
  p_limit integer
)
RETURNS TABLE (
  lab_id integer,
  name text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_clinical, portal, pg_temp
AS $$
DECLARE
  v_lim integer;
BEGIN
  IF p_user_id IS NULL THEN
    RETURN;
  END IF;
  v_lim := GREATEST(0, COALESCE(p_limit, 500));
  RETURN QUERY
  SELECT v.lab_id, v.name
  FROM omics_clinical.v_lab v
  ORDER BY v.name
  LIMIT v_lim;
END;
$$;
