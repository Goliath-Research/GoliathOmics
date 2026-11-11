-- Point clinical entity registry at omics/* domain classes (15_*) and retire duplicate Omics* roots.
-- Idempotent: safe on fresh DB (only omics/*) and upgraded DBs (legacy Omics* rows).

BEGIN;

DO $reconcile$
DECLARE
  rec record;
  v_canonical_id integer;
  v_legacy_id integer;
  v_legacy_table text;
BEGIN
  FOR rec IN
    SELECT *
    FROM (
      VALUES
        ('disease', 'omics/Disease', 'OmicsDisease'),
        ('customer', 'omics/Customer', 'OmicsCustomer'),
        ('patient', 'omics/Patient', 'OmicsPatient'),
        ('sample', 'omics/Sample', 'OmicsSample'),
        ('lab_batch', 'omics/LabBatch', 'OmicsLabBatch')
    ) AS t(entity_code, canonical_name, legacy_name)
  LOOP
    SELECT c."ID" INTO v_canonical_id
    FROM "Meta"."Classes" c
    WHERE c."Name" = rec.canonical_name
    LIMIT 1;

    IF v_canonical_id IS NULL THEN
      RAISE NOTICE 'skip reconcile %: canonical class % missing', rec.entity_code, rec.canonical_name;
      CONTINUE;
    END IF;

    SELECT c."ID" INTO v_legacy_id
    FROM "Meta"."Classes" c
    WHERE c."Name" = rec.legacy_name
      AND c."ID" <> v_canonical_id
    LIMIT 1;

    IF v_legacy_id IS NOT NULL THEN
      v_legacy_table := rec.legacy_name || 'Legacy';
      UPDATE "Meta"."Classes" l
      SET "TableName" = v_legacy_table
      WHERE l."ID" = v_legacy_id
        AND COALESCE(l."TableName", l."Name") IS NOT DISTINCT FROM (
          SELECT COALESCE(c."TableName", c."Name")
          FROM "Meta"."Classes" c
          WHERE c."ID" = v_canonical_id
        );

      UPDATE "Meta"."Classes" l
      SET "ParentID" = v_canonical_id
      WHERE l."ID" = v_legacy_id
        AND (l."ParentID" IS NULL OR l."ParentID" <> v_canonical_id);
    END IF;

    INSERT INTO omics_clinical.meta_class_registry (entity_code, class_id, class_name)
    VALUES (rec.entity_code, v_canonical_id, rec.canonical_name)
  ON CONFLICT (entity_code) DO UPDATE SET
      class_id = EXCLUDED.class_id,
      class_name = EXCLUDED.class_name;

    UPDATE portal.semantic_entity se
    SET meta_class_id = v_canonical_id
    WHERE se.namespace = 'omics'
      AND se.code = rec.entity_code
      AND (se.meta_class_id IS DISTINCT FROM v_canonical_id);
  END LOOP;

  PERFORM "Meta".sp_backfill_all_class_hierarchies();
END
$reconcile$;

COMMIT;
