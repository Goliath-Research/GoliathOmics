-- GoliathOmics R5 — lab inventory bridge to GoliathApp inv (Factory fac_mfg_inv pattern).
-- Prerequisites: GoliathApp inv_schema.sql + inv_api.sql on the database.
SET client_min_messages = warning;

CREATE SCHEMA IF NOT EXISTS omics_lab_inv;

COMMENT ON SCHEMA omics_lab_inv IS
  'Omics lab bridge: maps inv.stock_lot to portal labs/batches without forking inv DDL.';

CREATE TABLE IF NOT EXISTS omics_lab_inv.lab_stock_context (
  stock_lot_id integer PRIMARY KEY REFERENCES inv.stock_lot (stock_lot_id) ON DELETE CASCADE,
  organization_id integer NOT NULL,
  lab_id integer NULL REFERENCES portal."Labs" ("ID") ON DELETE SET NULL,
  batch_id integer NULL REFERENCES portal."Batches" ("ID") ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_lab_stock_context_org ON omics_lab_inv.lab_stock_context (organization_id);
CREATE INDEX IF NOT EXISTS ix_lab_stock_context_lab ON omics_lab_inv.lab_stock_context (lab_id);
CREATE INDEX IF NOT EXISTS ix_lab_stock_context_batch ON omics_lab_inv.lab_stock_context (batch_id);

CREATE OR REPLACE FUNCTION omics_lab_inv.sp_receive_for_lab(
  p_organization_id integer,
  p_user_id integer,
  p_lab_id integer,
  p_po_line_id integer,
  p_bin_id integer,
  p_qty numeric,
  p_lot_number text DEFAULT NULL,
  p_unit_price_override numeric DEFAULT NULL,
  OUT receipt_id integer,
  OUT stock_lot_id integer,
  OUT avg_unit_cost numeric
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_lab_inv, inv, portal, omics_clinical, pg_temp
AS $$
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  IF NOT omics_clinical.fn_user_can_access_organization(p_user_id, p_organization_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'organization scope denied');
  END IF;
  IF p_lab_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM portal."Labs" l WHERE l."ID" = p_lab_id
  ) THEN
    PERFORM portal.fn_semantic_raise('GS404', 'lab not found');
  END IF;

  SELECT r.receipt_id, r.stock_lot_id, r.avg_unit_cost
  INTO receipt_id, stock_lot_id, avg_unit_cost
  FROM inv.sp_stock_receive(
    p_organization_id,
    p_user_id,
    p_po_line_id,
    p_bin_id,
    p_qty,
    p_lot_number,
    p_unit_price_override
  ) AS r;

  INSERT INTO omics_lab_inv.lab_stock_context (
    stock_lot_id,
    organization_id,
    lab_id
  )
  VALUES (stock_lot_id, p_organization_id, p_lab_id)
  ON CONFLICT (stock_lot_id) DO UPDATE SET
    lab_id = COALESCE(EXCLUDED.lab_id, omics_lab_inv.lab_stock_context.lab_id),
    organization_id = EXCLUDED.organization_id;

  RETURN;
END;
$$;

CREATE OR REPLACE FUNCTION omics_lab_inv.sp_consume_for_batch(
  p_organization_id integer,
  p_user_id integer,
  p_batch_id integer,
  p_stock_lot_id integer,
  p_qty numeric,
  OUT stock_move_id bigint,
  OUT qty_on_hand numeric
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_lab_inv, inv, portal, omics_clinical, pg_temp
AS $$
DECLARE
  v_lab_id integer;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  IF NOT omics_clinical.fn_user_can_access_organization(p_user_id, p_organization_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'organization scope denied');
  END IF;

  SELECT b."LabID" INTO v_lab_id FROM portal."Batches" b WHERE b."ID" = p_batch_id;
  IF NOT FOUND THEN
    PERFORM portal.fn_semantic_raise('GS404', 'batch not found');
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM omics_lab_inv.lab_stock_context c
    WHERE c.stock_lot_id = p_stock_lot_id
      AND c.organization_id = p_organization_id
  ) THEN
    PERFORM portal.fn_semantic_raise('GS404', 'stock lot not linked to omics lab inventory');
  END IF;

  SELECT i.stock_move_id, i.qty_on_hand
  INTO stock_move_id, qty_on_hand
  FROM inv.sp_stock_issue_stub(
    p_organization_id,
    p_user_id,
    p_stock_lot_id,
    p_qty,
    'lab_batch',
    p_batch_id::text
  ) AS i;

  UPDATE omics_lab_inv.lab_stock_context
  SET batch_id = p_batch_id
  WHERE stock_lot_id = p_stock_lot_id
    AND batch_id IS NULL;

  RETURN;
END;
$$;
