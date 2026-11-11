-- GoliathOmics domain Meta classes (portal host package). Subclass platform/* parents only.
-- Prerequisites: GoliathApp workflow_engine/sql_pg/apply_schema.sh (includes platform_generic_meta_baseline.sql).

BEGIN;

DO $omics_classes$
DECLARE
  v_parent int;
  v_class int;
  v_pairs text[][] := ARRAY[
    ARRAY['omics/Customer', 'platform/Party'],
    ARRAY['omics/Sample', 'platform/Material'],
    ARRAY['omics/Study', 'platform/Activity'],
    ARRAY['omics/SequencingRun', 'platform/Batch'],
    ARRAY['omics/AlignmentResult', 'platform/Document']
  ];
  v_row text[];
  v_name text;
  v_parent_name text;
BEGIN
  FOREACH v_row SLICE 1 IN ARRAY v_pairs
  LOOP
    v_name := v_row[1];
    v_parent_name := v_row[2];
    SELECT c."ID" INTO v_parent FROM "Meta"."Classes" c WHERE c."Name" = v_parent_name LIMIT 1;
    IF v_parent IS NULL THEN
      RAISE EXCEPTION 'parent class % is required before Omics package', v_parent_name;
    END IF;
    INSERT INTO "Meta"."Classes" ("Name", "ParentID")
    SELECT v_name, v_parent
    WHERE NOT EXISTS (SELECT 1 FROM "Meta"."Classes" c WHERE c."Name" = v_name);
  END LOOP;

  PERFORM "Meta".sp_backfill_all_class_hierarchies();
END
$omics_classes$;

COMMIT;
