-- 21_omics_utc_timestamps.sql
--
-- Every GoliathOmics timestamp is stored as UTC, whatever the session time zone.
--
-- Omics timestamp columns are timestamptz. Older files defaulted and wrote them with
-- (now() AT TIME ZONE 'utc'): a zoneless UTC wall clock that PostgreSQL converts back to
-- timestamptz in the *session* time zone, so a session outside UTC stored instants shifted by its
-- offset. The files now use now(). This file rewrites the defaults existing databases still carry
-- (CREATE TABLE IF NOT EXISTS does not), and adds omics_clinical.fn_utc_timestamp_violations().
-- GoliathApp's platform_utc_timestamps.sql does the same for the platform schemas; this file covers
-- every schema in the database, including omics_clinical and omics_lab_inv.
--
-- Stored values are not touched: production writes come from UTC sessions and are already correct.
-- timestamp (without time zone) bookkeeping columns that hold a UTC wall clock are left alone.
-- Idempotent. Run after GoliathApp's apply_schema.sh and files 01-20.

CREATE SCHEMA IF NOT EXISTS omics_clinical;

DO $$
DECLARE
  r record;
  v_fixed int := 0;
BEGIN
  FOR r IN
    SELECT n.nspname, c.relname, a.attname, pg_get_expr(d.adbin, d.adrelid) AS expr
    FROM pg_attrdef d
    JOIN pg_class c ON c.oid = d.adrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
      AND c.relkind IN ('r', 'p')
      AND a.atttypid = 'timestamptz'::regtype
      AND NOT a.attisdropped
      AND pg_get_expr(d.adbin, d.adrelid) ~* '^\(?\s*(now|clock_timestamp|statement_timestamp|transaction_timestamp)\(\)\s+AT\s+TIME\s+ZONE\s+''utc''(::text)?\s*\)?$'
  LOOP
    EXECUTE format(
      'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT %s',
      r.nspname, r.relname, r.attname,
      CASE WHEN r.expr ~* 'clock_timestamp' THEN 'clock_timestamp()' ELSE 'now()' END
    );
    v_fixed := v_fixed + 1;
  END LOOP;
  RAISE NOTICE '21_omics_utc_timestamps: % column default(s) now store an absolute UTC instant', v_fixed;
END;
$$;

/*
  Timestamptz defaults, and functions, anywhere in the database that still derive a value from
  now() AT TIME ZONE 'utc' (formatting a UTC string with to_char is fine). Empty means every
  timestamp is stored as UTC in any session time zone.
*/
CREATE OR REPLACE FUNCTION omics_clinical.fn_utc_timestamp_violations()
RETURNS TABLE (object_kind text, object_name text, detail text)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT 'column default', format('%I.%I.%I', n.nspname, c.relname, a.attname), pg_get_expr(d.adbin, d.adrelid)
  FROM pg_attrdef d
  JOIN pg_class c ON c.oid = d.adrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
  WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
    AND a.atttypid = 'timestamptz'::regtype
    AND pg_get_expr(d.adbin, d.adrelid) ~* 'AT\s+TIME\s+ZONE|timezone\('
  UNION ALL
  SELECT 'function', format('%I.%I(%s)', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)),
         'derives a timestamp from now() AT TIME ZONE ''utc'''
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
    AND regexp_replace(p.prosrc, 'to_char\(\s*now\(\)\s+AT\s+TIME\s+ZONE\s+''utc''', '', 'gi')
        ~* '(now|clock_timestamp|statement_timestamp|transaction_timestamp)\(\)\s+AT\s+TIME\s+ZONE\s+''utc'''
$$;

REVOKE ALL ON FUNCTION omics_clinical.fn_utc_timestamp_violations() FROM PUBLIC;
