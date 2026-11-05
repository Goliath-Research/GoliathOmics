/*
  Omics clinical portal procedures (read paths for diseases, groups, samples).
  Depends on GoliathApp rbac_schema.sql and portal_clinical_schema.sql.
*/

CREATE OR REPLACE FUNCTION portal.sp_list_diseases()
RETURNS TABLE(
  id integer, name text, parent_id integer
)
LANGUAGE sql STABLE AS $$
  SELECT d."ID", d."Name"::text, d."ParentID"
  FROM portal."Diseases" d
  ORDER BY d."ID";
$$;

CREATE OR REPLACE FUNCTION portal.sp_list_groups_for_disease(
  p_disease_id integer,
  p_customer_id integer DEFAULT NULL
)
RETURNS TABLE(
  id integer, name text, description text, customer_id integer, sample_count bigint
)
LANGUAGE sql STABLE AS $$
  SELECT
    g."ID",
    g."Name"::text,
    g."Description"::text,
    g."CustomerID",
    COUNT(gs."SampleID") AS sample_count
  FROM portal."Groups" g
  LEFT JOIN portal."GroupSamples" gs ON gs."GroupID" = g."ID"
  LEFT JOIN portal."Samples" s ON s."ID" = gs."SampleID"
  WHERE (s."DiseaseID" = p_disease_id OR p_disease_id IS NULL)
    AND (p_customer_id IS NULL OR g."CustomerID" = p_customer_id)
  GROUP BY g."ID", g."Name", g."Description", g."CustomerID"
  ORDER BY g."Name";
$$;

CREATE OR REPLACE FUNCTION portal.sp_list_groups_for_study_enrollment(
  p_customer_id integer DEFAULT NULL
)
RETURNS TABLE(
  id integer, name text, description text, customer_id integer, sample_count bigint
)
LANGUAGE sql STABLE AS $$
  SELECT
    g."ID",
    g."Name"::text,
    g."Description"::text,
    g."CustomerID",
    COUNT(gs."SampleID") AS sample_count
  FROM portal."Groups" g
  LEFT JOIN portal."GroupSamples" gs ON gs."GroupID" = g."ID"
  WHERE (p_customer_id IS NULL OR g."CustomerID" = p_customer_id)
  GROUP BY g."ID", g."Name", g."Description", g."CustomerID"
  ORDER BY g."Name";
$$;

CREATE OR REPLACE FUNCTION portal.sp_list_group_samples_for_enrollment(
  p_group_id integer
)
RETURNS TABLE(
  portal_sample_id integer, patient_id integer, customer_id integer,
  disease_id integer, age numeric, bmi numeric
)
LANGUAGE sql STABLE AS $$
  SELECT
    s."ID"        AS portal_sample_id,
    s."PatientID",
    s."CustomerID",
    s."DiseaseID",
    s."Age",
    s."BMI"
  FROM portal."Samples" s
  JOIN portal."GroupSamples" gs ON gs."SampleID" = s."ID"
  WHERE gs."GroupID" = p_group_id
  ORDER BY s."ID";
$$;

CREATE OR REPLACE FUNCTION portal.sp_list_group_sample_names(
  p_group_id integer
)
RETURNS TABLE(
  portal_sample_id integer, lab_sample_id integer, processing_sample_key text
)
LANGUAGE sql STABLE AS $$
  SELECT
    s."ID"  AS portal_sample_id,
    ls."ID" AS lab_sample_id,
    ls."Sample"::text AS processing_sample_key
  FROM portal."Samples" s
  JOIN portal."GroupSamples" gs ON gs."SampleID" = s."ID"
  LEFT JOIN portal."LabSamples" ls ON ls."SampleID" = s."ID"
  WHERE gs."GroupID" = p_group_id
  ORDER BY s."ID", ls."ID";
$$;

-- Active organization scope → Omics customer (first match when many customers share an org).
CREATE OR REPLACE FUNCTION portal.sp_get_session_customer(
  p_session_id integer
)
RETURNS TABLE(
  session_id integer, user_id integer, scope_id integer,
  customer_id integer, customer_name text
)
LANGUAGE sql STABLE AS $$
  SELECT
    s."ID"            AS session_id,
    s."UserID"        AS user_id,
    s."ActiveScopeID" AS scope_id,
    c."ID"            AS customer_id,
    c."Name"::text    AS customer_name
  FROM "RBAC"."Sessions" s
  LEFT JOIN "RBAC"."Scopes" sc ON sc."ScopeID" = s."ActiveScopeID"
  LEFT JOIN portal."Customers" c ON c."OrganizationID" = sc."OrganizationID"
  WHERE s."ID" = p_session_id
  ORDER BY c."ID"
  LIMIT 1;
$$;
