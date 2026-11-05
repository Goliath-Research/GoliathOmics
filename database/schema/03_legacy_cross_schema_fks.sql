-- Cross-schema foreign keys for GoliathOmics portal clinical domain.
-- Omics → GoliathApp only (Organizations, resource_profile, cfg.analyte).
-- Platform shell FKs remain in GoliathApp legacy_cross_schema_fks.sql.

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_Batches_Labs_ID') THEN
    ALTER TABLE "portal"."Batches"
      ADD CONSTRAINT "FK_Batches_Labs_ID"
      FOREIGN KEY ("LabID") REFERENCES "portal"."Labs" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_Customers_Organizations_ID') THEN
    ALTER TABLE "portal"."Customers"
      ADD CONSTRAINT "FK_Customers_Organizations_ID"
      FOREIGN KEY ("OrganizationID") REFERENCES portal."Organizations" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_CustomerInstitutions_Customers_ID') THEN
    ALTER TABLE "portal"."CustomerInstitutions"
      ADD CONSTRAINT "FK_CustomerInstitutions_Customers_ID"
      FOREIGN KEY ("CustomerID") REFERENCES "portal"."Customers" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_CustomerInstitutions_Institutions_ID') THEN
    ALTER TABLE "portal"."CustomerInstitutions"
      ADD CONSTRAINT "FK_CustomerInstitutions_Institutions_ID"
      FOREIGN KEY ("InstitutionID") REFERENCES "portal"."Institutions" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_DiseaseDataSource_Disease') THEN
    ALTER TABLE "portal"."DiseaseDataSource"
      ADD CONSTRAINT "FK_DiseaseDataSource_Disease"
      FOREIGN KEY ("DiseaseID") REFERENCES "portal"."Diseases" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_DiseaseFieldContract_Disease') THEN
    ALTER TABLE "portal"."DiseaseFieldContract"
      ADD CONSTRAINT "FK_DiseaseFieldContract_Disease"
      FOREIGN KEY ("DiseaseID") REFERENCES "portal"."Diseases" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_Groups_Customers_ID') THEN
    ALTER TABLE "portal"."Groups"
      ADD CONSTRAINT "FK_Groups_Customers_ID"
      FOREIGN KEY ("CustomerID") REFERENCES "portal"."Customers" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_GroupSamples_Groups') THEN
    ALTER TABLE "portal"."GroupSamples"
      ADD CONSTRAINT "FK_GroupSamples_Groups"
      FOREIGN KEY ("GroupID") REFERENCES "portal"."Groups" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_GroupSamples_Samples') THEN
    ALTER TABLE "portal"."GroupSamples"
      ADD CONSTRAINT "FK_GroupSamples_Samples"
      FOREIGN KEY ("SampleID") REFERENCES "portal"."Samples" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_InstitutionLabs') THEN
    ALTER TABLE "portal"."InstitutionLabs"
      ADD CONSTRAINT "FK_InstitutionLabs"
      FOREIGN KEY ("InstitutionID") REFERENCES "portal"."Institutions" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_InstitutionLabs_Labs_ID') THEN
    ALTER TABLE "portal"."InstitutionLabs"
      ADD CONSTRAINT "FK_InstitutionLabs_Labs_ID"
      FOREIGN KEY ("LabID") REFERENCES "portal"."Labs" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_LabSamples_Batches_ID') THEN
    ALTER TABLE "portal"."LabSamples"
      ADD CONSTRAINT "FK_LabSamples_Batches_ID"
      FOREIGN KEY ("BatchID") REFERENCES "portal"."Batches" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_LabSamples_Samples') THEN
    ALTER TABLE "portal"."LabSamples"
      ADD CONSTRAINT "FK_LabSamples_Samples"
      FOREIGN KEY ("SampleID") REFERENCES "portal"."Samples" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_Patients_Institutions') THEN
    ALTER TABLE "portal"."Patients"
      ADD CONSTRAINT "FK_Patients_Institutions"
      FOREIGN KEY ("InstitutionID") REFERENCES "portal"."Institutions" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_portal_Samples_analyte') THEN
    ALTER TABLE "portal"."Samples"
      ADD CONSTRAINT "FK_portal_Samples_analyte"
      FOREIGN KEY (analyte_id) REFERENCES cfg.analyte (id);
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_project_Customers') THEN
    ALTER TABLE "portal"."project"
      ADD CONSTRAINT "FK_project_Customers"
      FOREIGN KEY ("customer_id") REFERENCES "portal"."Customers" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_project_resource_profile') THEN
    ALTER TABLE "portal"."project"
      ADD CONSTRAINT "FK_project_resource_profile"
      FOREIGN KEY ("archive_profile_key") REFERENCES portal.resource_profile (profile_key);
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SampleImportBatch_Customer') THEN
    ALTER TABLE "portal"."SampleImportBatch"
      ADD CONSTRAINT "FK_SampleImportBatch_Customer"
      FOREIGN KEY ("CustomerID") REFERENCES "portal"."Customers" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SampleImportBatch_Disease') THEN
    ALTER TABLE "portal"."SampleImportBatch"
      ADD CONSTRAINT "FK_SampleImportBatch_Disease"
      FOREIGN KEY ("DiseaseID") REFERENCES "portal"."Diseases" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SampleImportBatch_Institution') THEN
    ALTER TABLE "portal"."SampleImportBatch"
      ADD CONSTRAINT "FK_SampleImportBatch_Institution"
      FOREIGN KEY ("InstitutionID") REFERENCES "portal"."Institutions" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SampleImportRow_Batch') THEN
    ALTER TABLE "portal"."SampleImportRow"
      ADD CONSTRAINT "FK_SampleImportRow_Batch"
      FOREIGN KEY ("BatchID") REFERENCES "portal"."SampleImportBatch" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_Samples_Customers_ID') THEN
    ALTER TABLE "portal"."Samples"
      ADD CONSTRAINT "FK_Samples_Customers_ID"
      FOREIGN KEY ("CustomerID") REFERENCES "portal"."Customers" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_Samples_Diseases_ID') THEN
    ALTER TABLE "portal"."Samples"
      ADD CONSTRAINT "FK_Samples_Diseases_ID"
      FOREIGN KEY ("DiseaseID") REFERENCES "portal"."Diseases" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_Samples_Patients') THEN
    ALTER TABLE "portal"."Samples"
      ADD CONSTRAINT "FK_Samples_Patients"
      FOREIGN KEY ("PatientID") REFERENCES "portal"."Patients" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SamplesBreastCancer_Samples') THEN
    ALTER TABLE "portal"."SamplesBreastCancer"
      ADD CONSTRAINT "FK_SamplesBreastCancer_Samples"
      FOREIGN KEY ("ID") REFERENCES "portal"."Samples" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $fk$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SamplesProstateCancer_Samples') THEN
    ALTER TABLE "portal"."SamplesProstateCancer"
      ADD CONSTRAINT "FK_SamplesProstateCancer_Samples"
      FOREIGN KEY ("ID") REFERENCES "portal"."Samples" ("ID");
  END IF;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'skip FK: %', SQLERRM;
END $fk$;

DO $cfg$
BEGIN
  IF to_regclass('cfg.study_group_member') IS NOT NULL
     AND to_regclass('portal."Samples"') IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_cfg_sgm_portal_sample') THEN
    ALTER TABLE cfg.study_group_member
      ADD CONSTRAINT "FK_cfg_sgm_portal_sample"
      FOREIGN KEY (portal_sample_id) REFERENCES portal."Samples" ("ID");
  END IF;

  IF to_regclass('cfg.study_group_member') IS NOT NULL
     AND to_regclass('portal."LabSamples"') IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_cfg_sgm_lab_sample') THEN
    ALTER TABLE cfg.study_group_member
      ADD CONSTRAINT "FK_cfg_sgm_lab_sample"
      FOREIGN KEY (lab_sample_id) REFERENCES portal."LabSamples" ("ID");
  END IF;
END $cfg$;

DO $views$
BEGIN
  IF to_regclass('portal."Samples"') IS NOT NULL THEN
    EXECUTE 'CREATE OR REPLACE VIEW portal.samples AS SELECT * FROM portal."Samples"';
  END IF;
  IF to_regclass('portal."LabSamples"') IS NOT NULL THEN
    EXECUTE 'CREATE OR REPLACE VIEW portal.labsamples AS SELECT * FROM portal."LabSamples"';
  END IF;
END $views$;
