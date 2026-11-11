-- GoliathOmics — Meta classes subclassing GoliathApp platform/* parents (shared platform host).
-- Clinical Meta registration and semantic entities remain in 10_omics_meta_clinical_classes.sql
-- and 11_omics_meta_semantic_writes.sql; this file adds omics/* portal class names only.
-- Optional shared portal integration; skipped if its platform/* parent classes are absent.

DO $omics_classes$
DECLARE
  v_parent int;
  v_pairs text[][] := ARRAY[
    ARRAY['omics/Disease', 'platform/Entity', 'OmicsDisease'],
    ARRAY['omics/Customer', 'platform/Party', 'OmicsCustomer'],
    ARRAY['omics/Patient', 'platform/Person', 'OmicsPatient'],
    ARRAY['omics/Sample', 'platform/Material', 'OmicsSample'],
    ARRAY['omics/LabBatch', 'platform/Batch', 'OmicsLabBatch'],
    ARRAY['omics/Study', 'platform/Activity', 'OmicsStudy'],
    ARRAY['omics/SequencingRun', 'platform/Batch', 'OmicsSequencingRun'],
    ARRAY['omics/AlignmentResult', 'platform/Document', 'OmicsAlignmentResult']
  ];
  v_row text[];
  v_name text;
  v_parent_name text;
  v_table_name text;
BEGIN
  FOREACH v_row SLICE 1 IN ARRAY v_pairs
  LOOP
    v_parent_name := v_row[2];
    SELECT c."ID" INTO v_parent FROM "Meta"."Classes" c WHERE c."Name" = v_parent_name LIMIT 1;
    IF v_parent IS NULL THEN
      RAISE NOTICE 'platform parent class % is missing; skipping optional Omics portal Meta classes', v_parent_name;
      RETURN;
    END IF;
  END LOOP;

  FOREACH v_row SLICE 1 IN ARRAY v_pairs
  LOOP
    v_name := v_row[1];
    v_parent_name := v_row[2];
    v_table_name := v_row[3];
    SELECT c."ID" INTO v_parent FROM "Meta"."Classes" c WHERE c."Name" = v_parent_name LIMIT 1;
    IF v_parent IS NULL THEN
      RAISE EXCEPTION 'parent class % is required before 15_omics_meta_domain_classes.sql', v_parent_name;
    END IF;
    INSERT INTO "Meta"."Classes" ("Name", "ParentID", "TableName")
    SELECT v_name, v_parent, v_table_name
    WHERE NOT EXISTS (SELECT 1 FROM "Meta"."Classes" c WHERE c."Name" = v_name);

    UPDATE "Meta"."Classes" c
    SET "ParentID" = v_parent,
        "TableName" = COALESCE(c."TableName", v_table_name)
    WHERE c."Name" = v_name
      AND (
        c."ParentID" IS DISTINCT FROM v_parent
        OR c."TableName" IS DISTINCT FROM v_table_name
      );
  END LOOP;

  PERFORM "Meta".sp_backfill_all_class_hierarchies();
END
$omics_classes$;
