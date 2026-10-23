/*
  PostgreSQL parity for legacy portal clinical sample queries (MSSQL
  portal_clinical_api removed in P0b).

  Depends on portal_clinical_schema.sql and GoliathApp rbac_schema.sql.
  Lowercase views portal.samples / portal.labsamples come from legacy_cross_schema_fks.sql.
*/

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spGetSamplesByCustomer
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spgetsamplesbycustomer(
  p_customer_id int
)
RETURNS TABLE(
  id int, age numeric, height numeric, weight numeric, bmi numeric,
  disease text, stage text
)
LANGUAGE sql STABLE AS $$
  SELECT
    s."ID",
    s."Age",
    s."Height",
    s."Weight",
    s."BMI",
    d."Name"::text AS disease,
    s."Stage"::text
  FROM portal."Samples" s
  JOIN portal."Diseases" d ON d."ID" = s."DiseaseID"
  WHERE s."CustomerID" = p_customer_id;
$$;

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spGetProstateSamplesByCustomer
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spgetprostatesamplesbycustomer(
  p_customer_id int
)
RETURNS TABLE(
  patient_id int, age numeric, height numeric, weight numeric, bmi numeric,
  disease_id int, stage text, psa numeric, dre text, gleason_score text,
  reported_gleason text, equivalent_pattern text, real_gleason_score text,
  grade_group text, risk_level text
)
LANGUAGE sql STABLE AS $$
  SELECT
    s."PatientID",
    s."Age", s."Height", s."Weight", s."BMI",
    s."DiseaseID",
    s."Stage"::text,
    spc."PSA",
    spc."DRE"::text,
    spc."GleasonScore"::text,
    spc."GleasonScore"::text AS reported_gleason,
    NULL::text AS equivalent_pattern,
    NULL::text AS real_gleason_score,
    NULL::text AS grade_group,
    NULL::text AS risk_level
  FROM portal."Samples" s
  JOIN portal."SamplesProstateCancer" spc ON spc."ID" = s."ID"
  WHERE s."CustomerID" = p_customer_id;
$$;

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spGetGroupSamplesByCustomer
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spgetgroupsamplesbycustomer(
  p_customer_id int
)
RETURNS TABLE(id int, name text, description text)
LANGUAGE sql STABLE AS $$
  SELECT g."ID", g."Name"::text, g."Description"::text
  FROM portal."Groups" g
  WHERE g."CustomerID" = p_customer_id;
$$;

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spGetSamplesByGroup
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spgetsamplesbygroup(
  p_group_id int
)
RETURNS TABLE(
  patient_id int, age numeric, height numeric, weight numeric, bmi numeric,
  disease text, stage text
)
LANGUAGE sql STABLE AS $$
  SELECT
    s."PatientID",
    s."Age", s."Height", s."Weight", s."BMI",
    d."Name"::text AS disease,
    s."Stage"::text
  FROM portal."Samples" s
  JOIN portal."GroupSamples" gs ON gs."SampleID" = s."ID"
  JOIN portal."Diseases" d ON d."ID" = s."DiseaseID"
  WHERE gs."GroupID" = p_group_id;
$$;

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spGetAllInstitutionByCustomer
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spgetallinstitutionbycustomer(
  p_customer_id int
)
RETURNS TABLE(id int, name text)
LANGUAGE sql STABLE AS $$
  SELECT i."ID", i."Name"::text
  FROM portal."Institutions" i
  JOIN portal."CustomerInstitutions" ci ON ci."InstitutionID" = i."ID"
  WHERE ci."CustomerID" = p_customer_id;
$$;
