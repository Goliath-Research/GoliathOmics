/*
  Omics-owned flexible sample covariates on portal."Samples"."Extras".
  GoliathApp portal_sample_extras_schema.sql owns sample_field_contract only.
*/

CREATE SCHEMA IF NOT EXISTS portal;

ALTER TABLE portal."Samples"
  ADD COLUMN IF NOT EXISTS "Extras" jsonb NULL;
