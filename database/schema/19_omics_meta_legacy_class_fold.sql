-- GoliathOmics: fold the legacy Omics* clinical classes into their omics/* parents and type
-- the clinical reference fields as relations.
--
-- OmicsDisease, OmicsCustomer, OmicsPatient, OmicsSample and OmicsLabBatch are subclasses
-- (17_*) of omics/Disease, Customer, Patient, Sample and LabBatch with the same fields. The
-- omics/* classes are the one class per concept and own all 25 fields; the semantic entities
-- omics.disease, customer, patient, sample and lab_batch point at them.
--
-- Idempotent. On a database that still has a legacy class this file:
--   1. aborts (whole transaction) if the class has objects, object values, subclasses,
--      methods, links, collections, operations, registry or semantic entity rows, role
--      access rows, or if anything outside the class uses one of its own types;
--   2. repoints anything still typed meta/class/Omics* to the parent's meta/class/omics/* type;
--   3. drops its "Meta"."VOmics*Legacy" view, deletes its own type rows, then the class
--      (its field rows go with it). Nothing else is deleted.
-- Then, on every database, it types the reference fields as relations
-- (x-goliath-relation on a type derived from the field's previous type) and creates
-- omics_clinical.fn_semantic_attribute_fields(), used by scripts/verify_omics_semantic_fields.sh.
-- Requires 10_* (omics_clinical.fn_meta_ensure_wf_type) and 15_*/17_*.

BEGIN;

DO $fold$
DECLARE
  rec record;
  v_legacy integer;
  v_parent integer;
  v_parent_type bigint;
  v_refs text;
  v_n bigint;
BEGIN
  FOR rec IN
    SELECT * FROM (VALUES
      ('OmicsDisease', 'omics/Disease'),
      ('OmicsCustomer', 'omics/Customer'),
      ('OmicsPatient', 'omics/Patient'),
      ('OmicsSample', 'omics/Sample'),
      ('OmicsLabBatch', 'omics/LabBatch')
    ) AS t(legacy_name, parent_name)
  LOOP
    SELECT c."ID" INTO v_legacy FROM "Meta"."Classes" c WHERE c."Name" = rec.legacy_name;
    IF v_legacy IS NULL THEN
      CONTINUE;
    END IF;
    SELECT c."ID", c."TypeID" INTO v_parent, v_parent_type
    FROM "Meta"."Classes" c WHERE c."Name" = rec.parent_name;
    IF v_parent IS NULL THEN
      RAISE EXCEPTION 'fold %: parent class % is missing', rec.legacy_name, rec.parent_name;
    END IF;

    -- 1. Abort unless the legacy class is empty and unreferenced.
    SELECT string_agg(k || '=' || n, ', ') INTO v_refs
    FROM (
      SELECT 'objects' k, count(*) n FROM "Meta"."Objs" WHERE "ClassID" = v_legacy
      UNION ALL SELECT 'object_values', count(*) FROM "Meta"."ObjValues" WHERE "ClassID" = v_legacy
      UNION ALL SELECT 'subclasses', count(*) FROM "Meta"."Classes" WHERE "ParentID" = v_legacy
      UNION ALL SELECT 'methods', count(*) FROM "Meta"."Methods" WHERE "ClassID" = v_legacy
      UNION ALL SELECT 'links', count(*) FROM "Meta"."Links" WHERE v_legacy IN ("ClassID1", "ClassID2")
      UNION ALL SELECT 'meta_links', count(*) FROM "Meta"."MetaLinks" WHERE v_legacy IN ("ClassID", "TargetClassID")
      UNION ALL SELECT 'collections', count(*) FROM "Meta"."Collections" WHERE "ItemClassID" = v_legacy
      UNION ALL SELECT 'operations', count(*) FROM "Meta"."Operations" WHERE "ClassID" = v_legacy
      UNION ALL SELECT 'registry', count(*) FROM omics_clinical.meta_class_registry WHERE class_id = v_legacy
      UNION ALL SELECT 'semantic_entities', count(*) FROM portal.semantic_entity WHERE meta_class_id = v_legacy
      UNION ALL SELECT 'role_access', count(*) FROM portal."MetaClassRoleAccess" WHERE "ClassID" = v_legacy
      UNION ALL SELECT 'type_users', count(*) FROM (
        SELECT 1 FROM "Meta"."Fields" f
        WHERE f."TypeID" IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy) AND f."ClassID" <> v_legacy
        UNION ALL SELECT 1 FROM "Meta"."MethodParams" p
        WHERE p."TypeID" IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
        UNION ALL SELECT 1 FROM "Meta"."Methods" m
        WHERE m."ReturnTypeID" IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
        UNION ALL SELECT 1 FROM wf.workflow_action a
        WHERE a.input_type_id IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
           OR a.output_type_id IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
        UNION ALL SELECT 1 FROM cfg.kind_document_type d
        WHERE d.type_id IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
        UNION ALL SELECT 1 FROM wf.data_type_field df
        WHERE df.field_type_id IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
          AND df.data_type_id NOT IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
        UNION ALL SELECT 1 FROM wf.data_type t
        WHERE (t.base_type_id IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
            OR t.element_type_id IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy))
          AND t.class_id IS DISTINCT FROM v_legacy
      ) u
    ) c
    WHERE n > 0;
    IF v_refs IS NOT NULL THEN
      RAISE EXCEPTION 'fold %: legacy class is not empty or still referenced (%); nothing changed',
        rec.legacy_name, v_refs;
    END IF;

    -- Every legacy field must exist on the parent with the same name, so nothing is lost.
    SELECT string_agg(f."Name", ', ') INTO v_refs
    FROM "Meta"."Fields" f
    WHERE f."ClassID" = v_legacy
      AND NOT EXISTS (
        SELECT 1 FROM "Meta"."Fields" p WHERE p."ClassID" = v_parent AND p."Name" = f."Name"
      );
    IF v_refs IS NOT NULL THEN
      RAISE EXCEPTION 'fold %: fields % are not defined on %; nothing changed',
        rec.legacy_name, v_refs, rec.parent_name;
    END IF;

    -- 2. Repoint anything typed meta/class/<legacy> (the checks above found none outside the
    --    class; this keeps the file safe if a later version adds one).
    IF v_parent_type IS NOT NULL THEN
      UPDATE "Meta"."Fields" f SET "TypeID" = v_parent_type
      WHERE f."TypeID" IN (SELECT id FROM wf.data_type WHERE class_id = v_legacy)
        AND f."ClassID" <> v_legacy;
    END IF;

    -- 3. Drop the legacy view, the class's own type rows, then the class.
    EXECUTE format('DROP VIEW IF EXISTS "Meta".%I', 'V' || rec.legacy_name || 'Legacy');
    UPDATE "Meta"."Classes" SET "TypeID" = NULL WHERE "ID" = v_legacy;
    DELETE FROM wf.data_type WHERE class_id = v_legacy;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    DELETE FROM "Meta"."Classes" WHERE "ID" = v_legacy;
    RAISE NOTICE 'folded % into % (% type rows removed)', rec.legacy_name, rec.parent_name, v_n;
  END LOOP;
END
$fold$;

-- Reference fields typed as relations. Each relation type derives from the field's previous
-- type (same kind and JSON type, so the generated views do not change) and names its target.
DO $relations$
DECLARE
  rec record;
  v_class integer;
  v_field_type bigint;
  v_schema jsonb;
  v_type bigint;
BEGIN
  FOR rec IN
    SELECT * FROM (VALUES
      ('omics/Sample', 'patient_id', 'omics/ref/patient',
        '{"entity": "omics.patient", "class": "omics/Patient", "key": "patient_id"}'),
      ('omics/Sample', 'customer_id', 'omics/ref/customer',
        '{"entity": "omics.customer", "class": "omics/Customer", "key": "customer_id"}'),
      ('omics/Sample', 'disease_id', 'omics/ref/disease',
        '{"entity": "omics.disease", "class": "omics/Disease", "key": "disease_id"}'),
      ('omics/Disease', 'parent_id', 'omics/ref/disease',
        '{"entity": "omics.disease", "class": "omics/Disease", "key": "disease_id"}'),
      ('omics/LabBatch', 'lab_id', 'omics/ref/lab',
        '{"entity": "omics.lab", "class": "omics/Lab", "key": "lab_id"}'),
      ('omics/Customer', 'organization_id', 'omics/ref/organization',
        '{"entity": "core.organization", "key": "organization_id"}'),
      ('omics/Patient', 'institution_id', 'omics/ref/institution',
        '{"entity": "omics.institution", "class": "omics/Institution", "key": "institution_id"}')
    ) AS t(class_name, field_name, type_name, relation)
  LOOP
    SELECT c."ID" INTO v_class FROM "Meta"."Classes" c WHERE c."Name" = rec.class_name;
    IF v_class IS NULL THEN
      RAISE NOTICE 'relation %.%: class missing; skipped', rec.class_name, rec.field_name;
      CONTINUE;
    END IF;
    SELECT f."TypeID" INTO v_field_type
    FROM "Meta"."Fields" f WHERE f."ClassID" = v_class AND f."Name" = rec.field_name;
    IF v_field_type IS NULL THEN
      RAISE NOTICE 'relation %.%: field missing; skipped', rec.class_name, rec.field_name;
      CONTINUE;
    END IF;
    -- Already a relation type: keep it (re-runs and the meta_classes seed land here).
    IF EXISTS (SELECT 1 FROM wf.data_type WHERE id = v_field_type AND name = rec.type_name) THEN
      CONTINUE;
    END IF;
    SELECT jsonb_build_object('type', t.schema_json->'type')
             || jsonb_build_object('x-goliath-relation', rec.relation::jsonb)
    INTO v_schema
    FROM wf.data_type t WHERE t.id = v_field_type;
    v_type := omics_clinical.fn_meta_ensure_wf_type(rec.type_name, 'int', v_schema);
    UPDATE wf.data_type SET base_type_id = v_field_type
    WHERE id = v_type AND base_type_id IS NULL;
    UPDATE "Meta"."Fields" SET "TypeID" = v_type
    WHERE "ClassID" = v_class AND "Name" = rec.field_name;
  END LOOP;
END
$relations$;

-- One row per attribute of each omics semantic entity, with the class field it reads.
--   status 'field'     : column_name is a field on the entity's Meta class
--   status 'object_id' : column_name is the Meta object id ("ID") of a Meta-stored entity
--   status 'external'  : the entity is stored outside Meta (owner class only); no field expected
--   status 'missing'   : a Meta-stored entity reads a column that is not a field of its class
CREATE OR REPLACE FUNCTION omics_clinical.fn_semantic_attribute_fields()
RETURNS TABLE (
  entity text,
  attribute text,
  column_name text,
  owning_class text,
  field_id integer,
  field_type text,
  relation jsonb,
  status text
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT
    e.namespace || '.' || e.code,
    a.code,
    a.column_name,
    c."Name",
    f."ID",
    t.name,
    t.schema_json -> 'x-goliath-relation',
    CASE
      WHEN f."ID" IS NOT NULL THEN 'field'
      WHEN c."ID" IS NULL
        OR e.storage_relation IS DISTINCT FROM format('"Meta".%I', 'V' || coalesce(c."TableName", c."Name"))
        THEN 'external'
      WHEN a.column_name = 'ID' THEN 'object_id'
      ELSE 'missing'
    END
  FROM portal.semantic_entity e
  JOIN portal.semantic_attribute a ON a.entity_id = e.id
  LEFT JOIN "Meta"."Classes" c ON c."ID" = e.meta_class_id
  LEFT JOIN "Meta"."Fields" f ON f."ClassID" = c."ID" AND f."Name" = a.column_name
  LEFT JOIN wf.data_type t ON t.id = f."TypeID"
  WHERE e.namespace = 'omics'
  ORDER BY e.code, a.id;
$$;

COMMIT;
