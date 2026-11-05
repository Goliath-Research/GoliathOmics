-- GoliathOmics R5 PR2 — lab reagent semantic list/receive/consume (namespace omics).
SET client_min_messages = warning;

CREATE OR REPLACE VIEW omics_lab_inv.v_lab_reagent AS
SELECT
  sl.stock_lot_id,
  sl.organization_id,
  c.lab_id,
  sl.item_id,
  sl.item_number,
  sl.lot_number,
  sl.qty_on_hand,
  sl.unit_cost,
  sl.location_code,
  sl.bin_code
FROM inv.v_stock_lot sl
LEFT JOIN omics_lab_inv.lab_stock_context c ON c.stock_lot_id = sl.stock_lot_id;

CREATE OR REPLACE FUNCTION omics_lab_inv.fn_semantic_lab_reagent_list(
  p_user_id integer,
  p_params jsonb,
  p_limit integer
)
RETURNS TABLE (
  stock_lot_id integer,
  organization_id integer,
  lab_id integer,
  item_id integer,
  item_number text,
  lot_number text,
  qty_on_hand numeric,
  unit_cost numeric,
  location_code text,
  bin_code text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = omics_lab_inv, inv, portal, omics_clinical, pg_temp
AS $$
DECLARE
  v_org_id integer;
  v_filter_lab_id integer;
  v_filter_item_id integer;
  v_lim integer;
BEGIN
  IF p_user_id IS NULL THEN
    RETURN;
  END IF;
  v_org_id := NULLIF(p_params->>'organization_id', '')::integer;
  v_filter_lab_id := NULLIF(p_params->>'lab_id', '')::integer;
  v_filter_item_id := NULLIF(p_params->>'item_id', '')::integer;
  IF v_org_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS400', 'organization_id required');
  END IF;
  IF NOT omics_clinical.fn_user_can_access_organization(p_user_id, v_org_id) THEN
    PERFORM portal.fn_semantic_raise('GS403', 'organization scope denied');
  END IF;
  v_lim := GREATEST(0, COALESCE(p_limit, 500));
  RETURN QUERY
  SELECT
    v.stock_lot_id,
    v.organization_id,
    v.lab_id,
    v.item_id,
    v.item_number,
    v.lot_number,
    v.qty_on_hand,
    v.unit_cost,
    v.location_code,
    v.bin_code
  FROM omics_lab_inv.v_lab_reagent v
  WHERE v.organization_id = v_org_id
    AND (v_filter_lab_id IS NULL OR v.lab_id = v_filter_lab_id)
    AND (v_filter_item_id IS NULL OR v.item_id = v_filter_item_id)
    AND v.qty_on_hand > 0
  ORDER BY v.item_number, v.stock_lot_id
  LIMIT v_lim;
END;
$$;

CREATE OR REPLACE FUNCTION omics_lab_inv.fn_semantic_lab_reagent_receive(
  p_user_id integer,
  p_payload jsonb
)
RETURNS portal.omics_lab_reagent_row
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_lab_inv, inv, portal, omics_clinical, pg_temp
AS $$
DECLARE
  v_org_id integer;
  v_lab_id integer;
  v_po_line_id integer;
  v_bin_id integer;
  v_qty numeric;
  v_lot_number text;
  v_stock_lot_id integer;
  v_row portal.omics_lab_reagent_row;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  v_org_id := NULLIF(p_payload->>'organization_id', '')::integer;
  v_lab_id := NULLIF(p_payload->>'lab_id', '')::integer;
  v_po_line_id := NULLIF(p_payload->>'po_line_id', '')::integer;
  v_bin_id := NULLIF(p_payload->>'bin_id', '')::integer;
  v_qty := NULLIF(p_payload->>'qty', '')::numeric;
  v_lot_number := NULLIF(p_payload->>'lot_number', '');

  SELECT r.stock_lot_id
  INTO v_stock_lot_id
  FROM omics_lab_inv.sp_receive_for_lab(
    v_org_id,
    p_user_id,
    v_lab_id,
    v_po_line_id,
    v_bin_id,
    v_qty,
    v_lot_number,
    NULLIF(p_payload->>'unit_price_override', '')::numeric
  ) AS r;

  SELECT
    v.stock_lot_id,
    v.organization_id,
    v.lab_id,
    v.item_id,
    v.item_number,
    v.lot_number,
    v.qty_on_hand,
    v.unit_cost,
    v.location_code,
    v.bin_code
  INTO v_row
  FROM omics_lab_inv.v_lab_reagent v
  WHERE v.stock_lot_id = v_stock_lot_id;

  RETURN v_row;
END;
$$;

CREATE OR REPLACE FUNCTION omics_lab_inv.fn_semantic_lab_reagent_consume(
  p_user_id integer,
  p_payload jsonb
)
RETURNS portal.omics_lab_reagent_row
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = omics_lab_inv, inv, portal, omics_clinical, pg_temp
AS $$
DECLARE
  v_org_id integer;
  v_batch_id integer;
  v_stock_lot_id integer;
  v_qty numeric;
  v_row portal.omics_lab_reagent_row;
BEGIN
  IF p_user_id IS NULL THEN
    PERFORM portal.fn_semantic_raise('GS401', 'user required');
  END IF;
  v_org_id := NULLIF(p_payload->>'organization_id', '')::integer;
  v_batch_id := NULLIF(p_payload->>'batch_id', '')::integer;
  v_stock_lot_id := NULLIF(p_payload->>'stock_lot_id', '')::integer;
  v_qty := NULLIF(p_payload->>'qty', '')::numeric;

  PERFORM omics_lab_inv.sp_consume_for_batch(
    v_org_id,
    p_user_id,
    v_batch_id,
    v_stock_lot_id,
    v_qty
  );

  SELECT
    v.stock_lot_id,
    v.organization_id,
    v.lab_id,
    v.item_id,
    v.item_number,
    v.lot_number,
    v.qty_on_hand,
    v.unit_cost,
    v.location_code,
    v.bin_code
  INTO v_row
  FROM omics_lab_inv.v_lab_reagent v
  WHERE v.stock_lot_id = v_stock_lot_id;

  RETURN v_row;
END;
$$;
