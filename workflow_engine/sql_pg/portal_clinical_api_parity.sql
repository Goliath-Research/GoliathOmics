/*
  PostgreSQL parity for portal.sp* clinical-data procedures extracted from
  workflow_engine/sql_mssql/portal_clinical_api.sql.

  Quoted PascalCase identifiers match portal_clinical_schema.sql.  Lowercase
  view aliases (portal.samples, portal.labsamples) are available from
  portal_sample_extras_schema.sql.

  Depends on:
    portal_clinical_schema.sql  – portal."Samples", portal."LabSamples",
                                   portal."Diseases", portal."Groups",
                                   portal."GroupSamples", portal."NavTree",
                                   portal."Customers", portal."Patients",
                                   portal."SamplesProstateCancer",
                                   portal."Institutions", portal."CustomerInstitutions"
    rbac_schema.sql             – "RBAC"."Sessions", portal."Role2Node",
                                   portal."viewUserAllRoles"
*/

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spGetCollectionItems
-- Dynamic attribute-pivot over Meta.CollectionItem.  The Meta schema is
-- MSSQL-only (sys.columns catalogue); a faithful port is impractical.
-- Callers should migrate to the cfg/jsonb-backed collections layer.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spgetcollectionitems(
  p_collection_name text
)
RETURNS TABLE(
  collection_item_id int,
  parent_id int,
  idx int,
  primary_value_string text,
  primary_value_number numeric,
  value_object_id int,
  attributes jsonb
)
LANGUAGE plpgsql STABLE AS $$
BEGIN
  -- Meta schema does not exist in PG; returning empty set with note.
  RAISE EXCEPTION 'not ported: portal.spGetCollectionItems requires Meta schema (MSSQL-only). Migrate to cfg jsonb collections.';
END;
$$;

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spNavTreeMove  (full port – NavTree path maintenance)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spnavtreemove(
  p_node_id int,
  p_new_parent_id int DEFAULT -1,
  p_new_seq smallint DEFAULT NULL
)
RETURNS TABLE(
  node_id int, parent_id int, seq smallint, node_path text, sort_path text
)
LANGUAGE plpgsql AS $$
DECLARE
  v_current_parent  int;
  v_current_seq     smallint;
  v_old_node_path   text;
  v_old_sort_path   text;
  v_scope_root      text;
  v_slash_pos       int;
  v_parent_node_path text;
  v_parent_sort_path text;
  v_eff_seq         smallint;
  v_new_node_path   text;
  v_new_sort_path   text;
  v_sort_seg        text;
BEGIN
  IF p_node_id IS NULL OR p_node_id <= 0 THEN
    RAISE EXCEPTION 'ValidationError: NodeID is required.';
  END IF;
  IF p_new_parent_id IS NULL OR (p_new_parent_id <> -1 AND p_new_parent_id <= 0) THEN
    RAISE EXCEPTION 'ValidationError: NewParentID must be -1 (root) or a valid node ID.';
  END IF;
  IF p_new_parent_id = p_node_id THEN
    RAISE EXCEPTION 'BusinessRule: A node cannot be its own parent.';
  END IF;

  SELECT n."ParentID", n."Seq", n."NodePath", n."SortPath"
  INTO v_current_parent, v_current_seq, v_old_node_path, v_old_sort_path
  FROM portal."NavTree" n WHERE n."ID" = p_node_id
  FOR UPDATE;

  IF v_old_node_path IS NULL THEN
    RAISE EXCEPTION 'BusinessRule: Node does not exist or lacks a NodePath.';
  END IF;
  IF v_old_sort_path IS NULL OR btrim(v_old_sort_path) = '' THEN
    RAISE EXCEPTION 'BusinessRule: Node does not have a SortPath.';
  END IF;

  v_slash_pos := position('/' IN substr(v_old_node_path, 2)) + 1;
  IF v_slash_pos > 1 THEN
    v_scope_root := left(v_old_node_path, v_slash_pos - 1);
  ELSE
    v_scope_root := v_old_node_path;
  END IF;

  IF p_new_parent_id <> -1 THEN
    SELECT p."NodePath", p."SortPath"
    INTO v_parent_node_path, v_parent_sort_path
    FROM portal."NavTree" p WHERE p."ID" = p_new_parent_id
    FOR UPDATE;

    IF v_parent_node_path IS NULL THEN
      RAISE EXCEPTION 'BusinessRule: Target parent does not exist or lacks a NodePath.';
    END IF;
    IF v_parent_sort_path IS NULL OR btrim(v_parent_sort_path) = '' THEN
      RAISE EXCEPTION 'BusinessRule: Target parent does not have a SortPath.';
    END IF;
    IF v_parent_node_path = v_old_node_path OR v_parent_node_path LIKE v_old_node_path || '/%' THEN
      RAISE EXCEPTION 'BusinessRule: Cannot move node under itself or its descendants.';
    END IF;
    IF NOT (v_parent_node_path = v_scope_root OR v_parent_node_path LIKE v_scope_root || '/%') THEN
      RAISE EXCEPTION 'BusinessRule: Cannot move node outside its scope tree.';
    END IF;
  END IF;

  IF v_old_node_path = v_scope_root AND p_new_parent_id <> -1 THEN
    RAISE EXCEPTION 'BusinessRule: Scope root node cannot be moved under another parent.';
  END IF;
  IF p_new_parent_id = -1 AND v_old_node_path <> v_scope_root THEN
    RAISE EXCEPTION 'BusinessRule: Cannot detach a scope node from its root tree.';
  END IF;

  v_eff_seq := COALESCE(p_new_seq, v_current_seq);
  v_sort_seg := lpad(v_eff_seq::text, 5, '0');

  IF p_new_parent_id = v_current_parent AND v_eff_seq = v_current_seq THEN
    RETURN QUERY
    SELECT p_node_id, v_current_parent, v_current_seq, v_old_node_path, v_old_sort_path;
    RETURN;
  END IF;

  IF p_new_parent_id = -1 THEN
    v_new_node_path := '/' || p_node_id::text;
    v_new_sort_path := '/' || v_sort_seg;
  ELSE
    v_new_node_path := v_parent_node_path || '/' || p_node_id::text;
    v_new_sort_path := v_parent_sort_path || '/' || v_sort_seg;
  END IF;

  -- Update descendants first (path prefix replace)
  UPDATE portal."NavTree" d
  SET "NodePath" = v_new_node_path || substr(d."NodePath", length(v_old_node_path) + 1),
      "SortPath" = v_new_sort_path || substr(d."SortPath", length(v_old_sort_path) + 1)
  WHERE d."NodePath" LIKE v_old_node_path || '/%';

  -- Update the node itself
  UPDATE portal."NavTree"
  SET "ParentID" = p_new_parent_id,
      "Seq"      = v_eff_seq,
      "NodePath" = v_new_node_path,
      "SortPath" = v_new_sort_path
  WHERE "ID" = p_node_id;

  RETURN QUERY
  SELECT p_node_id, p_new_parent_id, v_eff_seq, v_new_node_path, v_new_sort_path;
END;
$$;

-- ──────────────────────────────────────────────────────────────────────────────
-- portal.spNavTreeDeleteNode
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portal.spnavtreedeletenode(
  p_node_id int
)
RETURNS TABLE(deleted_node_id int, deleted_node_path text, deleted_rows bigint)
LANGUAGE plpgsql AS $$
DECLARE
  v_node_path text;
  v_parent_id int;
  v_rows      bigint;
BEGIN
  IF p_node_id IS NULL OR p_node_id <= 0 THEN
    RAISE EXCEPTION 'ValidationError: NodeID is required.';
  END IF;

  SELECT n."NodePath", n."ParentID"
  INTO v_node_path, v_parent_id
  FROM portal."NavTree" n WHERE n."ID" = p_node_id
  FOR UPDATE;

  IF v_node_path IS NULL THEN
    RAISE EXCEPTION 'BusinessRule: Node does not exist or lacks a NodePath.';
  END IF;
  IF v_parent_id = -1 THEN
    RAISE EXCEPTION 'BusinessRule: Scope root node cannot be deleted.';
  END IF;

  DELETE FROM portal."NavTree" n
  WHERE n."NodePath" = v_node_path OR n."NodePath" LIKE v_node_path || '/%';
  GET DIAGNOSTICS v_rows = ROW_COUNT;

  RETURN QUERY SELECT p_node_id, v_node_path, v_rows;
END;
$$;

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
