-- 20_omics_clinical_record_history.sql
--
-- History for every GoliathOmics clinical record: "history must exist, no gaps".
--
-- GoliathOmics clinical data lives in relational tables (portal."Patients", "Samples",
-- "Institutions", ... from 01_portal_clinical_schema.sql, and the omics_clinical.* support
-- tables). GoliathApp's Meta history (ObjValuesHistory) covers Meta objects only, so these
-- tables need their own history. This file:
--
--   * creates omics_clinical.record_history: one row per insert, update or delete, with the
--     row key, the before and after row documents, the acting portal user and session
--     (goliath.actor_user_id / goliath.actor_session_id, set by GoliathApp's session resolve
--     when present), the database login role and the transaction id;
--   * installs an AFTER trigger on every tracked table; updates that change nothing are not
--     recorded;
--   * on first install, records the rows that already exist as 'baseline' entries, so the
--     history is complete from that moment;
--   * makes record_history a GoliathApp ledger (append-only, hash-chained, verified by
--     portal.fn_ledger_verify_all) when GoliathApp's History H1 is installed
--     (portal.sp_ledger_register). Without H1 the table is still append-only through its own
--     guard; re-running this file after GoliathApp is updated registers the ledger and chains
--     the rows already recorded.
--
-- Idempotent. Run after GoliathApp's apply_schema.sh and files 01-19.

CREATE SCHEMA IF NOT EXISTS omics_clinical;

CREATE TABLE IF NOT EXISTS omics_clinical.record_history (
  history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  table_name text NOT NULL,
  row_key jsonb NOT NULL,
  op text NOT NULL CHECK (op IN ('baseline', 'insert', 'update', 'delete')),
  before_doc jsonb NULL,
  after_doc jsonb NULL,
  actor_user_id integer NULL,
  actor_session_id integer NULL,
  db_role text NOT NULL DEFAULT session_user,
  record_txid bigint NOT NULL DEFAULT txid_current(),
  recorded_at_utc timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_omics_record_history_row
  ON omics_clinical.record_history (table_name, row_key);

COMMENT ON TABLE omics_clinical.record_history IS
  'Append-only history of GoliathOmics clinical records (insert/update/delete with before and after documents).';

REVOKE ALL ON omics_clinical.record_history FROM PUBLIC;

-- Integer from a transaction-local setting, NULL when unset or not an integer.
CREATE OR REPLACE FUNCTION omics_clinical.fn_setting_int(p_name text)
RETURNS integer
LANGUAGE sql
STABLE
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT CASE WHEN v ~ '^[0-9]{1,9}$' THEN v::integer END
  FROM (SELECT current_setting(p_name, true) AS v) s;
$$;

-- AFTER ROW trigger. TG_ARGV lists the table's primary-key columns.
CREATE OR REPLACE FUNCTION omics_clinical.fn_record_history()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_old jsonb;
  v_new jsonb;
  v_src jsonb;
  v_key jsonb := '{}'::jsonb;
  v_col text;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    v_old := to_jsonb(OLD);
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    v_new := to_jsonb(NEW);
  END IF;
  IF TG_OP = 'UPDATE' AND v_old = v_new THEN
    RETURN NULL;
  END IF;
  v_src := COALESCE(v_new, v_old);
  FOREACH v_col IN ARRAY TG_ARGV LOOP
    v_key := v_key || jsonb_build_object(v_col, v_src -> v_col);
  END LOOP;
  INSERT INTO omics_clinical.record_history (
    table_name, row_key, op, before_doc, after_doc, actor_user_id, actor_session_id
  )
  VALUES (
    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, v_key, lower(TG_OP), v_old, v_new,
    omics_clinical.fn_setting_int('goliath.actor_user_id'),
    omics_clinical.fn_setting_int('goliath.actor_session_id')
  );
  RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION omics_clinical.fn_record_history() FROM PUBLIC;

-- Append-only guard used until GoliathApp's ledger takes over.
CREATE OR REPLACE FUNCTION omics_clinical.fn_record_history_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'omics_clinical.record_history is append-only'
    USING ERRCODE = 'insufficient_privilege';
END;
$$;

DO $install$
DECLARE
  v_tables text[] := ARRAY[
    'portal."Batches"', 'portal."CustomerInstitutions"', 'portal."Customers"',
    'portal."DiseaseDataSource"', 'portal."DiseaseFieldContract"', 'portal."Diseases"',
    'portal."Groups"', 'portal."GroupSamples"', 'portal."InstitutionLabs"',
    'portal."Institutions"', 'portal."Labs"', 'portal."LabSamples"', 'portal."Patients"',
    'portal."project"', 'portal."SampleImportBatch"', 'portal."SampleImportRow"',
    'portal."Samples"', 'portal."SamplesBreastCancer"', 'portal."SamplesProstateCancer"'
  ];
  v_name text;
  v_rel regclass;
  v_pk text[];
  v_args text;
  v_key_expr text;
BEGIN
  -- Every omics_clinical table except the history itself.
  SELECT v_tables || COALESCE(array_agg(format('%I.%I', n.nspname, c.relname) ORDER BY c.relname), ARRAY[]::text[])
  INTO v_tables
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'omics_clinical' AND c.relkind IN ('r', 'p') AND c.relname <> 'record_history';

  FOREACH v_name IN ARRAY v_tables LOOP
    v_rel := to_regclass(v_name);
    CONTINUE WHEN v_rel IS NULL;

    SELECT array_agg(a.attname::text ORDER BY k.ord)
    INTO v_pk
    FROM pg_index i
    CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
    WHERE i.indrelid = v_rel AND i.indisprimary;
    IF v_pk IS NULL THEN
      RAISE EXCEPTION 'record history: % has no primary key', v_name;
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_trigger WHERE tgrelid = v_rel AND tgname = 'trg_omics_record_history'
    ) THEN
      -- First install for this table: the rows that exist now are the baseline.
      SELECT string_agg(format('%L, d -> %L', c, c), ', ') INTO v_key_expr FROM unnest(v_pk) AS c;
      EXECUTE format(
        'INSERT INTO omics_clinical.record_history (table_name, row_key, op, after_doc) '
        'SELECT %L, jsonb_build_object(%s), ''baseline'', d '
        'FROM (SELECT to_jsonb(t) AS d FROM %s t) s ORDER BY 2',
        (SELECT format('%s.%s', n.nspname, c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = v_rel),
        v_key_expr, v_rel
      );
      SELECT string_agg(quote_literal(c), ', ') INTO v_args FROM unnest(v_pk) AS c;
      EXECUTE format(
        'CREATE TRIGGER trg_omics_record_history AFTER INSERT OR UPDATE OR DELETE ON %s '
        'FOR EACH ROW EXECUTE FUNCTION omics_clinical.fn_record_history(%s)',
        v_rel, v_args
      );
    END IF;
  END LOOP;
END;
$install$;

DO $ledger$
BEGIN
  IF to_regprocedure('portal.sp_ledger_register(text,regclass,text,text,text[])') IS NOT NULL THEN
    -- GoliathApp History H1: hand append-only enforcement and the hash chain to the ledger.
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'omics_clinical.record_history'::regclass
               AND tgname IN ('trg_omics_record_history_guard', 'trg_omics_record_history_no_truncate')) THEN
      DROP TRIGGER IF EXISTS trg_omics_record_history_guard ON omics_clinical.record_history;
      DROP TRIGGER IF EXISTS trg_omics_record_history_no_truncate ON omics_clinical.record_history;
    END IF;
    PERFORM portal.sp_ledger_register(
      'omics_clinical.record_history',
      'omics_clinical.record_history'::regclass,
      'history_id',
      'GoliathOmics clinical record history (insert/update/delete with before and after documents)'
    );
  ELSE
    RAISE NOTICE 'GoliathApp History H1 (portal.sp_ledger_register) is not installed: omics_clinical.record_history is append-only but not hash-chained. Re-run this file after updating GoliathApp.';
    CREATE OR REPLACE TRIGGER trg_omics_record_history_guard
      BEFORE UPDATE OR DELETE ON omics_clinical.record_history
      FOR EACH ROW EXECUTE FUNCTION omics_clinical.fn_record_history_guard();
    CREATE OR REPLACE TRIGGER trg_omics_record_history_no_truncate
      BEFORE TRUNCATE ON omics_clinical.record_history
      FOR EACH STATEMENT EXECUTE FUNCTION omics_clinical.fn_record_history_guard();
  END IF;
END;
$ledger$;
