# Lab inventory (R5)

GoliathOmics links methylation lab consumables to the shared GoliathApp **`inv`** module through the thin **`omics_lab_inv`** bridge. Omics does not ship `inv` DDL; apply GoliathApp `inv_schema.sql` / `inv_api.sql` first (R1 platform pin).

## Architecture

| Layer | Location |
|-------|----------|
| Platform inventory | GoliathApp `inv.*`, semantic namespace `inv` |
| Omics bridge | `database/schema/13_omics_lab_inv_bridge.sql` — `lab_stock_context`, `sp_receive_for_lab`, `sp_consume_for_batch` |
| Semantic API | `database/schema/14_omics_inv_semantic.sql` — `omics.lab_reagent.*` requests |
| NavTree | `goliath-omics-seed-nav` — **Lab inventory** under **Labs** |

Receive wraps **`inv.sp_stock_receive`** and records `lab_id` on `omics_lab_inv.lab_stock_context`. Batch consumption wraps **`inv.sp_stock_issue_stub`** with `ref_type='lab_batch'`.

## Operator setup (test-bed / greenfield)

After GoliathApp + Omics domain schema apply:

```bash
pip install -e domain
goliath-omics-seed-meta-classes    # if Meta clinical cutover is in use
goliath-omics-seed-semantic
goliath-omics-seed-nav
```

Seed org-scoped `inv.item`, supplier, PO, location/bin via standard `inv` procs or portal UI (see GoliathApp inventory docs).

## Semantic requests (namespace `omics`)

| Request | Permission | Purpose |
|---------|------------|---------|
| `omics.lab_reagent.list` | `omics.lab_inventory.read` | On-hand lots by organization (optional `lab_id`, `item_id`) |
| `omics.lab_reagent.receive` | `omics.lab_inventory.write` | Receive PO line into bin; link to lab |
| `omics.lab_reagent.consume` | `omics.lab_inventory.write` | Issue quantity to a processing batch |

Platform **`inv.item.list`** / **`inv.stock_lot.by_item`** remain available for purchasing views; prefer **`omics.lab_reagent.list`** for lab-operator frames tied to `portal."Labs"`.

## SamplePrep audit hook (optional)

DomainPrograms may record consumed kit lots in node **`actionConfig`** without changing science binaries, for example:

```json
{
  "lab_stock_lot_id": 12345,
  "lab_batch_id": 67
}
```

Workers ignore unknown keys; the portal or a future audit action can persist the reference after `omics.lab_reagent.consume`.

## Related

- [Semantic layer](../architecture/semantic-layer.md)
- [Refresh plan R5](../refresh-plan.md)
- GoliathApp [inventory module](https://goliath-research.github.io/GoliathApp/) (platform docs)
