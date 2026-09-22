-- Versioned cfg.analyte catalog + study/sample FKs (PostgreSQL).
-- Prerequisites: cfg_registry_tables.sql, cfg_repo_api.sql, cfg_assay_procedure_links.sql

CREATE TABLE IF NOT EXISTS cfg.analyte (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name text NOT NULL,
  version text NOT NULL DEFAULT '1',
  status varchar(32) NOT NULL DEFAULT 'draft',
  content_hash text NOT NULL,
  document_json jsonb NOT NULL,
  created_at_utc timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  updated_at_utc timestamptz NULL,
  CONSTRAINT uq_cfg_analyte_name_version UNIQUE (name, version),
  CONSTRAINT ck_cfg_analyte_status CHECK (status IN ('draft', 'published', 'retired'))
);

ALTER TABLE cfg.study
  ADD COLUMN IF NOT EXISTS default_analyte_id bigint NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_cfg_study_default_analyte'
  ) THEN
    ALTER TABLE cfg.study
      ADD CONSTRAINT fk_cfg_study_default_analyte
      FOREIGN KEY (default_analyte_id) REFERENCES cfg.analyte (id);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_cfg_study_default_analyte ON cfg.study (default_analyte_id);

ALTER TABLE cfg.assay_procedure
  ADD COLUMN IF NOT EXISTS analyte_id bigint NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_cfg_ap_analyte_id'
  ) THEN
    ALTER TABLE cfg.assay_procedure
      ADD CONSTRAINT fk_cfg_ap_analyte_id
      FOREIGN KEY (analyte_id) REFERENCES cfg.analyte (id);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_cfg_ap_analyte_id ON cfg.assay_procedure (analyte_id);

-- Soft: portal.Samples exists as a table on Azure SQL; on PG it is often a view.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'portal' AND c.relname = 'samples' AND c.relkind = 'r'
  ) THEN
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'portal' AND table_name = 'samples' AND column_name = 'analyte_id'
    ) THEN
      ALTER TABLE portal.samples ADD COLUMN analyte_id bigint NULL;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint WHERE conname = 'fk_portal_samples_analyte'
    ) THEN
      ALTER TABLE portal.samples
        ADD CONSTRAINT fk_portal_samples_analyte
        FOREIGN KEY (analyte_id) REFERENCES cfg.analyte (id);
    END IF;
    CREATE INDEX IF NOT EXISTS ix_portal_samples_analyte ON portal.samples (analyte_id);
  END IF;
END $$;

CREATE OR REPLACE VIEW cfg.v_analyte AS
SELECT
  a.id AS analyte_id,
  a.name AS analyte_name,
  a.version AS analyte_version,
  a.status,
  a.document_json->'catalog'->>'title' AS catalog_title,
  a.document_json->'catalog'->>'visibility' AS catalog_visibility,
  a.document_json->'catalog'->>'lifecycle' AS catalog_lifecycle,
  a.document_json->'aliases' AS aliases
FROM cfg.analyte a;

DROP VIEW IF EXISTS cfg.v_assay_procedure;
CREATE OR REPLACE VIEW cfg.v_assay_procedure AS
SELECT
  ap.id AS assay_procedure_id,
  ap.name AS procedure_name,
  ap.version AS procedure_version,
  ap.status,
  ap.primary_analyte,
  ap.analyte_id,
  an.name AS analyte_name,
  ap.default_pipeline_profile_id,
  pp.name AS default_pipeline_profile_name,
  ap.sample_prep_program_id,
  sp.name AS sample_prep_program_name,
  ap.lifecycle_program_id,
  lp.name AS lifecycle_program_name,
  ap.document_json->'catalog'->>'title' AS catalog_title,
  ap.document_json->'catalog'->>'visibility' AS catalog_visibility,
  ap.document_json->'catalog'->>'lifecycle' AS catalog_lifecycle
FROM cfg.assay_procedure ap
LEFT JOIN cfg.analyte an ON an.id = ap.analyte_id
LEFT JOIN cfg.pipeline_profile pp ON pp.id = ap.default_pipeline_profile_id
LEFT JOIN cfg.domain_program sp ON sp.id = ap.sample_prep_program_id
LEFT JOIN cfg.domain_program lp ON lp.id = ap.lifecycle_program_id;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'cfg' AND p.proname = 'cfg_repo_bind_assay_procedure'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION cfg.cfg_repo_bind_assay_procedure(
  p_procedure_name text,
  p_procedure_version text DEFAULT '1',
  p_primary_analyte text DEFAULT NULL,
  p_default_pipeline_profile_name text DEFAULT NULL,
  p_sample_prep_program_name text DEFAULT NULL,
  p_lifecycle_program_name text DEFAULT NULL
)
RETURNS TABLE(
  id bigint,
  name text,
  primary_analyte text,
  analyte_id bigint,
  default_pipeline_profile_id bigint,
  sample_prep_program_id bigint,
  lifecycle_program_id bigint
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_ap_id bigint;
  v_profile_id bigint;
  v_sp_id bigint;
  v_lc_id bigint;
  v_analyte_id bigint;
BEGIN
  SELECT ap.id INTO v_ap_id
  FROM cfg.assay_procedure ap
  WHERE ap.name = p_procedure_name
    AND (p_procedure_version IS NULL OR ap.version = p_procedure_version)
  ORDER BY ap.id DESC
  LIMIT 1;
  IF v_ap_id IS NULL THEN
    RAISE EXCEPTION 'cfg.assay_procedure not found: %', p_procedure_name;
  END IF;

  IF p_default_pipeline_profile_name IS NOT NULL AND btrim(p_default_pipeline_profile_name) <> '' THEN
    SELECT pp.id INTO v_profile_id
    FROM cfg.pipeline_profile pp
    WHERE pp.name = p_default_pipeline_profile_name
    ORDER BY CASE pp.status WHEN 'published' THEN 0 WHEN 'retired' THEN 1 ELSE 2 END, pp.id DESC
    LIMIT 1;
  END IF;

  IF p_sample_prep_program_name IS NOT NULL AND btrim(p_sample_prep_program_name) <> '' THEN
    SELECT dp.id INTO v_sp_id
    FROM cfg.domain_program dp
    WHERE dp.name = p_sample_prep_program_name
    ORDER BY CASE dp.status WHEN 'published' THEN 0 ELSE 1 END, dp.id DESC
    LIMIT 1;
  END IF;

  IF p_lifecycle_program_name IS NOT NULL AND btrim(p_lifecycle_program_name) <> '' THEN
    SELECT dp.id INTO v_lc_id
    FROM cfg.domain_program dp
    WHERE dp.name = p_lifecycle_program_name
    ORDER BY CASE dp.status WHEN 'published' THEN 0 ELSE 1 END, dp.id DESC
    LIMIT 1;
  END IF;

  IF p_primary_analyte IS NOT NULL AND btrim(p_primary_analyte) <> '' THEN
    SELECT a.id INTO v_analyte_id
    FROM cfg.analyte a
    WHERE a.name = p_primary_analyte
      AND a.status IN ('published', 'retired')
    ORDER BY a.id DESC
    LIMIT 1;
  END IF;

  UPDATE cfg.assay_procedure ap
  SET primary_analyte = COALESCE(p_primary_analyte, ap.primary_analyte),
      analyte_id = COALESCE(v_analyte_id, ap.analyte_id),
      default_pipeline_profile_id = COALESCE(v_profile_id, ap.default_pipeline_profile_id),
      sample_prep_program_id = COALESCE(v_sp_id, ap.sample_prep_program_id),
      lifecycle_program_id = COALESCE(v_lc_id, ap.lifecycle_program_id),
      updated_at_utc = (now() AT TIME ZONE 'utc')
  WHERE ap.id = v_ap_id;

  RETURN QUERY
  SELECT ap.id, ap.name, ap.primary_analyte, ap.analyte_id, ap.default_pipeline_profile_id,
         ap.sample_prep_program_id, ap.lifecycle_program_id
  FROM cfg.assay_procedure ap
  WHERE ap.id = v_ap_id;
END;
$$;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'cfg' AND p.proname = 'cfg_repo_backfill_study_analyte_defaults'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION cfg.cfg_repo_backfill_study_analyte_defaults()
RETURNS TABLE(studies_updated bigint)
LANGUAGE plpgsql
AS $$
DECLARE
  v_count bigint;
BEGIN
  UPDATE cfg.study s
  SET default_analyte_id = a.id,
      updated_at_utc = (now() AT TIME ZONE 'utc')
  FROM LATERAL (
    SELECT x.id
    FROM cfg.analyte x
    WHERE x.name = s.document_json->'regulatory'->>'primary_analyte'
      AND x.status IN ('published', 'retired')
    ORDER BY x.id DESC
    LIMIT 1
  ) a
  WHERE s.default_analyte_id IS NULL
    AND NULLIF(btrim(s.document_json->'regulatory'->>'primary_analyte'), '') IS NOT NULL;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  studies_updated := v_count;
  RETURN NEXT;
END;
$$;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'portal' AND p.proname = 'sp_list_analytes'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION portal.sp_list_analytes(p_published_only boolean DEFAULT false)
RETURNS TABLE(
  id bigint,
  name text,
  version text,
  status text,
  content_hash text,
  document_json jsonb,
  created_at_utc timestamptz,
  updated_at_utc timestamptz
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    a.id, a.name, a.version, a.status::text, a.content_hash, a.document_json,
    a.created_at_utc, a.updated_at_utc
  FROM cfg.analyte a
  WHERE (NOT p_published_only OR a.status = 'published')
  ORDER BY a.name, a.version;
$$;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'portal' AND p.proname = 'sp_get_analyte'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION portal.sp_get_analyte(
  p_name text,
  p_version text DEFAULT NULL
)
RETURNS TABLE(
  id bigint,
  name text,
  version text,
  status text,
  content_hash text,
  document_json jsonb,
  created_at_utc timestamptz,
  updated_at_utc timestamptz
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    a.id, a.name, a.version, a.status::text, a.content_hash, a.document_json,
    a.created_at_utc, a.updated_at_utc
  FROM cfg.analyte a
  WHERE a.name = p_name
    AND (p_version IS NULL OR a.version = p_version)
  ORDER BY a.id DESC
  LIMIT 1;
$$;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'portal' AND p.proname = 'sp_list_analyte_catalog'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION portal.sp_list_analyte_catalog(
  p_include_advanced boolean DEFAULT false
)
RETURNS TABLE(
  id bigint,
  name text,
  version text,
  status text,
  title text,
  summary text,
  visibility text,
  lifecycle text,
  family text,
  replaced_by text,
  aliases jsonb
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    a.id,
    a.name,
    a.version,
    a.status::text,
    a.document_json->'catalog'->>'title',
    a.document_json->'catalog'->>'summary',
    a.document_json->'catalog'->>'visibility',
    a.document_json->'catalog'->>'lifecycle',
    a.document_json->'catalog'->>'family',
    a.document_json->'catalog'->>'replacedBy',
    a.document_json->'aliases'
  FROM cfg.analyte a
  WHERE a.status = 'published'
    AND a.document_json->'catalog'->>'lifecycle' = 'active'
    AND (
      a.document_json->'catalog'->>'visibility' = 'operator'
      OR (p_include_advanced AND a.document_json->'catalog'->>'visibility' = 'advanced')
    )
  ORDER BY a.name, a.version;
$$;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'portal' AND p.proname = 'sp_set_sample_analyte'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION portal.sp_set_sample_analyte(
  p_portal_sample_id int,
  p_analyte text DEFAULT NULL,
  p_analyte_id bigint DEFAULT NULL
)
RETURNS TABLE(
  portal_sample_id int,
  analyte_id bigint,
  analyte_name text
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_resolved bigint := p_analyte_id;
BEGIN
  IF to_regclass('portal.samples') IS NULL THEN
    RAISE EXCEPTION 'portal.Samples not found';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM portal.samples WHERE id = p_portal_sample_id) THEN
    RAISE EXCEPTION 'portal.Samples not found: %', p_portal_sample_id;
  END IF;

  IF v_resolved IS NULL AND p_analyte IS NOT NULL AND btrim(p_analyte) <> '' THEN
    SELECT a.id INTO v_resolved
    FROM cfg.analyte a
    WHERE a.name = p_analyte
      AND a.status IN ('published', 'retired')
    ORDER BY a.id DESC
    LIMIT 1;
    IF v_resolved IS NULL THEN
      RAISE EXCEPTION 'cfg.analyte not found: %', p_analyte;
    END IF;
  END IF;

  IF p_analyte IS NOT NULL AND btrim(p_analyte) = '' THEN
    v_resolved := NULL;
  END IF;

  UPDATE portal.samples SET analyte_id = v_resolved WHERE id = p_portal_sample_id;

  RETURN QUERY
  SELECT s.id, s.analyte_id, a.name
  FROM portal.samples s
  LEFT JOIN cfg.analyte a ON a.id = s.analyte_id
  WHERE s.id = p_portal_sample_id;
END;
$$;

-- Enrollment picker is Azure SQL–primary. PG stub: filter by study analyte when portal.samples exists.
DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'portal' AND p.proname = 'sp_list_samples_for_study_enrollment'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION portal.sp_list_samples_for_study_enrollment(
  p_customer_id int DEFAULT NULL,
  p_study_row_id bigint DEFAULT NULL,
  p_analyte_id bigint DEFAULT NULL
)
RETURNS TABLE(
  portal_sample_id int,
  analyte_id bigint,
  analyte_name text
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_filter bigint := p_analyte_id;
BEGIN
  IF v_filter IS NULL AND p_study_row_id IS NOT NULL THEN
    SELECT s.default_analyte_id INTO v_filter FROM cfg.study s WHERE s.id = p_study_row_id;
  END IF;

  IF to_regclass('portal.samples') IS NULL THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT s.id, s.analyte_id, a.name
  FROM portal.samples s
  LEFT JOIN cfg.analyte a ON a.id = s.analyte_id
  WHERE (v_filter IS NULL OR s.analyte_id = v_filter)
  ORDER BY s.id;
EXCEPTION
  WHEN undefined_column OR undefined_table THEN
    RETURN;
END;
$$;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'portal' AND p.proname = 'sp_get_study_process_defaults'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION portal.sp_get_study_process_defaults(p_study_row_id bigint)
RETURNS TABLE(
  study_row_id bigint,
  study_name text,
  study_version text,
  pipeline_profile text,
  pipeline_procedure text,
  analyte text,
  research_mode text,
  default_pipeline_profile_id bigint,
  default_assay_procedure_id bigint,
  default_analyte_id bigint,
  pipeline_profile_title text,
  pipeline_procedure_title text,
  analyte_title text
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    s.id,
    s.name,
    s.version,
    COALESCE(pp.name, s.document_json->>'pipelineProfile'),
    COALESCE(ap.name, s.document_json->>'pipelineProcedure'),
    COALESCE(an.name, s.document_json->'regulatory'->>'primary_analyte'),
    s.document_json->>'researchMode',
    s.default_pipeline_profile_id,
    s.default_assay_procedure_id,
    s.default_analyte_id,
    pp.document_json->'catalog'->>'title',
    ap.document_json->'catalog'->>'title',
    an.document_json->'catalog'->>'title'
  FROM cfg.study s
  LEFT JOIN cfg.pipeline_profile pp ON pp.id = s.default_pipeline_profile_id
  LEFT JOIN cfg.assay_procedure ap ON ap.id = s.default_assay_procedure_id
  LEFT JOIN cfg.analyte an ON an.id = s.default_analyte_id
  WHERE s.id = p_study_row_id;
$$;

DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'portal' AND p.proname = 'sp_set_study_process_defaults'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION portal.sp_set_study_process_defaults(
  p_study_row_id bigint,
  p_pipeline_profile text DEFAULT NULL,
  p_pipeline_procedure text DEFAULT NULL,
  p_research_mode text DEFAULT NULL,
  p_analyte text DEFAULT NULL,
  p_allow_advanced boolean DEFAULT false
)
RETURNS TABLE(
  study_row_id bigint,
  study_name text,
  study_version text,
  pipeline_profile text,
  pipeline_procedure text,
  analyte text,
  research_mode text,
  default_pipeline_profile_id bigint,
  default_assay_procedure_id bigint,
  default_analyte_id bigint,
  pipeline_profile_title text,
  pipeline_procedure_title text,
  analyte_title text
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_doc jsonb;
  v_profile_id bigint;
  v_assay_id bigint;
  v_analyte_id bigint;
BEGIN
  SELECT document_json INTO v_doc FROM cfg.study WHERE id = p_study_row_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'cfg.study not found: %', p_study_row_id;
  END IF;
  v_doc := COALESCE(v_doc, '{}'::jsonb);

  IF p_pipeline_profile IS NOT NULL AND btrim(p_pipeline_profile) <> '' THEN
    SELECT p.id INTO v_profile_id
    FROM cfg.pipeline_profile p
    WHERE p.name = p_pipeline_profile
      AND p.status = 'published'
      AND p.document_json->'catalog'->>'lifecycle' = 'active'
      AND (
        p.document_json->'catalog'->>'visibility' = 'operator'
        OR (p_allow_advanced AND p.document_json->'catalog'->>'visibility' = 'advanced')
      )
    ORDER BY p.id DESC
    LIMIT 1;
    IF v_profile_id IS NULL THEN
      RAISE EXCEPTION 'pipeline profile not in operator/advanced catalog: %', p_pipeline_profile;
    END IF;
  END IF;

  IF p_pipeline_procedure IS NOT NULL AND btrim(p_pipeline_procedure) <> '' THEN
    SELECT p.id INTO v_assay_id
    FROM cfg.assay_procedure p
    WHERE p.name = p_pipeline_procedure
      AND p.status = 'published'
      AND p.document_json->'catalog'->>'lifecycle' = 'active'
      AND (
        p.document_json->'catalog'->>'visibility' = 'operator'
        OR (p_allow_advanced AND p.document_json->'catalog'->>'visibility' = 'advanced')
      )
    ORDER BY p.id DESC
    LIMIT 1;
    IF v_assay_id IS NULL THEN
      RAISE EXCEPTION 'assay procedure not in operator/advanced catalog: %', p_pipeline_procedure;
    END IF;
  END IF;

  IF p_analyte IS NOT NULL AND btrim(p_analyte) <> '' THEN
    SELECT a.id INTO v_analyte_id
    FROM cfg.analyte a
    WHERE a.name = p_analyte
      AND a.status = 'published'
      AND a.document_json->'catalog'->>'lifecycle' = 'active'
      AND (
        a.document_json->'catalog'->>'visibility' = 'operator'
        OR (p_allow_advanced AND a.document_json->'catalog'->>'visibility' = 'advanced')
      )
    ORDER BY a.id DESC
    LIMIT 1;
    IF v_analyte_id IS NULL THEN
      RAISE EXCEPTION 'analyte not in operator/advanced catalog: %', p_analyte;
    END IF;
  END IF;

  IF p_pipeline_profile IS NOT NULL THEN
    IF btrim(p_pipeline_profile) = '' THEN
      v_doc := v_doc - 'pipelineProfile';
    ELSE
      v_doc := jsonb_set(v_doc, '{pipelineProfile}', to_jsonb(p_pipeline_profile), true);
    END IF;
  END IF;
  IF p_pipeline_procedure IS NOT NULL THEN
    IF btrim(p_pipeline_procedure) = '' THEN
      v_doc := v_doc - 'pipelineProcedure';
    ELSE
      v_doc := jsonb_set(v_doc, '{pipelineProcedure}', to_jsonb(p_pipeline_procedure), true);
    END IF;
  END IF;
  IF p_research_mode IS NOT NULL THEN
    IF btrim(p_research_mode) = '' THEN
      v_doc := v_doc - 'researchMode';
    ELSE
      v_doc := jsonb_set(v_doc, '{researchMode}', to_jsonb(p_research_mode), true);
    END IF;
  END IF;
  IF p_analyte IS NOT NULL THEN
    IF btrim(p_analyte) = '' THEN
      IF v_doc ? 'regulatory' THEN
        v_doc := jsonb_set(v_doc, '{regulatory}', (v_doc->'regulatory') - 'primary_analyte', true);
      END IF;
    ELSE
      v_doc := jsonb_set(
        CASE WHEN v_doc ? 'regulatory' THEN v_doc
             ELSE jsonb_set(v_doc, '{regulatory}', '{}'::jsonb, true)
        END,
        '{regulatory,primary_analyte}',
        to_jsonb(p_analyte),
        true
      );
    END IF;
  END IF;

  UPDATE cfg.study
  SET document_json = v_doc,
      content_hash = md5(v_doc::text),
      default_pipeline_profile_id = CASE
        WHEN p_pipeline_profile IS NULL THEN default_pipeline_profile_id
        WHEN btrim(p_pipeline_profile) = '' THEN NULL
        ELSE v_profile_id
      END,
      default_assay_procedure_id = CASE
        WHEN p_pipeline_procedure IS NULL THEN default_assay_procedure_id
        WHEN btrim(p_pipeline_procedure) = '' THEN NULL
        ELSE v_assay_id
      END,
      default_analyte_id = CASE
        WHEN p_analyte IS NULL THEN default_analyte_id
        WHEN btrim(p_analyte) = '' THEN NULL
        ELSE v_analyte_id
      END,
      updated_at_utc = (now() AT TIME ZONE 'utc')
  WHERE id = p_study_row_id;

  RETURN QUERY SELECT * FROM portal.sp_get_study_process_defaults(p_study_row_id);
END;
$$;

-- Membership hard-filter when portal.Samples.analyte_id is present.
DO $drop$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig, p.prokind
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'cfg' AND p.proname = 'cfg_repo_set_study_group_members'
  LOOP
    IF r.prokind = 'p' THEN
      EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.sig);
    ELSE
      EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END IF;
  END LOOP;
END $drop$;

CREATE OR REPLACE FUNCTION cfg.cfg_repo_set_study_group_members(
  p_study_group_id bigint,
  p_members jsonb
)
RETURNS TABLE(
  id bigint,
  study_group_id bigint,
  portal_sample_id int,
  lab_sample_id int,
  processing_sample_key text
)
LANGUAGE plpgsql
AS $$
DECLARE
  elem jsonb;
  v_portal int;
  v_lab int;
  v_key text;
  v_resolved text;
  v_study_analyte bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM cfg.study_group g WHERE g.id = p_study_group_id) THEN
    RAISE EXCEPTION 'cfg.study_group not found: %', p_study_group_id;
  END IF;

  SELECT s.default_analyte_id INTO v_study_analyte
  FROM cfg.study_group g
  INNER JOIN cfg.study s ON s.id = g.study_row_id
  WHERE g.id = p_study_group_id;

  DELETE FROM cfg.study_group_member WHERE study_group_id = p_study_group_id;

  FOR elem IN SELECT * FROM jsonb_array_elements(COALESCE(p_members, '[]'::jsonb))
  LOOP
    v_portal := (elem->>'portalSampleId')::int;
    v_lab := NULLIF(elem->>'labSampleId', '')::int;
    v_key := NULLIF(btrim(elem->>'processingSampleKey'), '');
    v_resolved := v_key;
    IF v_resolved IS NULL THEN
      RAISE EXCEPTION 'cannot resolve processing_sample_key for portalSampleId=%', v_portal;
    END IF;

    IF v_study_analyte IS NOT NULL AND to_regclass('portal.samples') IS NOT NULL THEN
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM portal.samples samp
          WHERE samp.id = v_portal AND samp.analyte_id = v_study_analyte
        ) THEN
          RAISE EXCEPTION
            'sample analyte mismatch: portalSampleId=% must match study default_analyte_id',
            v_portal;
        END IF;
      EXCEPTION
        WHEN undefined_column THEN
          NULL; -- analyte_id not present on this PG stub
      END;
    END IF;

    INSERT INTO cfg.study_group_member (
      study_group_id, portal_sample_id, lab_sample_id, processing_sample_key
    ) VALUES (p_study_group_id, v_portal, v_lab, v_resolved);
  END LOOP;

  UPDATE cfg.study_group SET updated_at_utc = (now() AT TIME ZONE 'utc')
  WHERE id = p_study_group_id;

  RETURN QUERY
  SELECT m.id, m.study_group_id, m.portal_sample_id, m.lab_sample_id, m.processing_sample_key
  FROM cfg.study_group_member m
  WHERE m.study_group_id = p_study_group_id
  ORDER BY m.processing_sample_key;
END;
$$;
