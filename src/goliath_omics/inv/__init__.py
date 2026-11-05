"""Omics lab inventory bridge (R5).

SQL procedures live in ``database/schema/13_omics_lab_inv_bridge.sql`` and
``14_omics_inv_semantic.sql``. Semantic specs are in ``goliath_omics.semantic.catalog``.

Optional SamplePrep nodes may include ``lab_stock_lot_id`` in ``actionConfig`` for
audit; see ``docs/operators/lab-inventory.md``.
"""
