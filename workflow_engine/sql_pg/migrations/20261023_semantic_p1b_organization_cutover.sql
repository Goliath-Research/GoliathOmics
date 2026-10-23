-- Semantic layer P1b: organization tenancy cutover for databases upgraded from pre-P1a.
-- Not in apply_schema.sh — run once before the first P1b release apply on an existing DB.
-- Fresh greenfield installs use GoliathApp P1a + Omics apply_schema.sh only.

BEGIN;

-- ── portal.Customers.OrganizationID ───────────────────────────────────────────
DO $cust$
BEGIN
  IF to_regclass('portal."Customers"') IS NULL THEN
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'portal' AND table_name = 'Customers' AND column_name = 'OrganizationID'
  ) THEN
    ALTER TABLE portal."Customers" ADD COLUMN "OrganizationID" integer NULL;
  END IF;

  INSERT INTO portal."Organizations" ("Name", "Active", "CreatedAtUtc")
  SELECT c."Name", true, now() AT TIME ZONE 'utc'
  FROM portal."Customers" c
  WHERE c."OrganizationID" IS NULL
    AND NOT EXISTS (
      SELECT 1 FROM portal."Organizations" o WHERE o."Name" = c."Name"
    );

  UPDATE portal."Customers" c
  SET "OrganizationID" = o."ID"
  FROM portal."Organizations" o
  WHERE c."OrganizationID" IS NULL
    AND o."Name" = c."Name";

  IF EXISTS (SELECT 1 FROM portal."Customers" WHERE "OrganizationID" IS NULL) THEN
    RAISE EXCEPTION 'P1b cutover: portal.Customers still has rows without OrganizationID';
  END IF;

  ALTER TABLE portal."Customers" ALTER COLUMN "OrganizationID" SET NOT NULL;
END $cust$;

-- ── Contract.Contracts.CustomerID → OrganizationID ───────────────────────────
DO $contract$
BEGIN
  IF to_regclass('"Contract"."Contracts"') IS NULL THEN
    RETURN;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'Contract' AND table_name = 'Contracts' AND column_name = 'CustomerID'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'Contract' AND table_name = 'Contracts' AND column_name = 'OrganizationID'
  ) THEN
    ALTER TABLE "Contract"."Contracts" ADD COLUMN "OrganizationID" integer NULL;

    UPDATE "Contract"."Contracts" ct
    SET "OrganizationID" = c."OrganizationID"
    FROM portal."Customers" c
    WHERE ct."CustomerID" = c."ID";

    IF EXISTS (SELECT 1 FROM "Contract"."Contracts" WHERE "OrganizationID" IS NULL) THEN
      RAISE EXCEPTION 'P1b cutover: Contract.Contracts could not map CustomerID to OrganizationID';
    END IF;

    ALTER TABLE "Contract"."Contracts" DROP CONSTRAINT IF EXISTS "FK_Contracts_Customers_ID";
    ALTER TABLE "Contract"."Contracts" DROP COLUMN "CustomerID";
    ALTER TABLE "Contract"."Contracts" ALTER COLUMN "OrganizationID" SET NOT NULL;
  END IF;
END $contract$;

-- ── RBAC.Scopes institution/lab → organization ───────────────────────────────
DO $scopes$
DECLARE
  rec record;
  v_org_id integer;
BEGIN
  IF to_regclass('"RBAC"."Scopes"') IS NULL THEN
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'RBAC' AND table_name = 'Scopes' AND column_name = 'InstitutionID'
  ) THEN
    RETURN;
  END IF;

  FOR rec IN
    SELECT s."ScopeID", s."InstitutionID", s."LabID", s."Name"
    FROM "RBAC"."Scopes" s
    WHERE s."OrganizationID" IS NULL
      AND (s."InstitutionID" IS NOT NULL OR s."LabID" IS NOT NULL)
  LOOP
    v_org_id := NULL;

    IF rec."InstitutionID" IS NOT NULL THEN
      SELECT c."OrganizationID"
      INTO v_org_id
      FROM portal."CustomerInstitutions" ci
      INNER JOIN portal."Customers" c ON c."ID" = ci."CustomerID"
      WHERE ci."InstitutionID" = rec."InstitutionID"
      ORDER BY c."ID"
      LIMIT 1;
    ELSIF rec."LabID" IS NOT NULL THEN
      SELECT c."OrganizationID"
      INTO v_org_id
      FROM portal."InstitutionLabs" il
      INNER JOIN portal."CustomerInstitutions" ci ON ci."InstitutionID" = il."InstitutionID"
      INNER JOIN portal."Customers" c ON c."ID" = ci."CustomerID"
      WHERE il."LabID" = rec."LabID"
      ORDER BY c."ID"
      LIMIT 1;
    END IF;

    IF v_org_id IS NULL THEN
      INSERT INTO portal."Organizations" ("Name", "Active", "CreatedAtUtc")
      VALUES (
        COALESCE(rec."Name", 'Migrated scope ' || rec."ScopeID"::text),
        true,
        now() AT TIME ZONE 'utc'
      )
      RETURNING "ID" INTO v_org_id;
    END IF;

    UPDATE "RBAC"."Scopes"
    SET "ScopeType" = 'organization',
        "OrganizationID" = v_org_id,
        "InstitutionID" = NULL,
        "LabID" = NULL
    WHERE "ScopeID" = rec."ScopeID";
  END LOOP;

  ALTER TABLE "RBAC"."Scopes" DROP CONSTRAINT IF EXISTS "FK_RBAC_Scopes_InstitutionID";
  ALTER TABLE "RBAC"."Scopes" DROP CONSTRAINT IF EXISTS "FK_RBAC_Scopes_LabID";
  ALTER TABLE "RBAC"."Scopes" DROP COLUMN IF EXISTS "InstitutionID";
  ALTER TABLE "RBAC"."Scopes" DROP COLUMN IF EXISTS "LabID";
END $scopes$;

COMMIT;
