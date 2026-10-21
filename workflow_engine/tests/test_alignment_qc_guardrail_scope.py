"""Instance core_guardrails fold into the baked alignment_qc window."""

from __future__ import annotations

from workflow_context import _fold_core_guardrails, build_resolved_config_scope_vars


def test_top_level_core_guardrails_fold_into_alignment_qc() -> None:
    action: dict = {}
    _fold_core_guardrails(action, {"max_at_dropout": 100.0})
    assert action["alignment_qc"]["core_guardrails"]["max_at_dropout"] == 100.0

    nested = {"alignment_qc": {"core_guardrails": {"max_at_dropout": 7.5, "max_gc_dropout": 9.0}}}
    _fold_core_guardrails(nested, {"max_at_dropout": 100.0, "max_gc_dropout": 1.0})
    assert nested["alignment_qc"]["core_guardrails"]["max_at_dropout"] == 7.5
    assert nested["alignment_qc"]["core_guardrails"]["max_gc_dropout"] == 9.0


def test_baked_alignment_qc_keeps_instance_max_at_dropout_over_site() -> None:
    action: dict = {}
    _fold_core_guardrails(action, {"max_at_dropout": 100.0})
    baked = build_resolved_config_scope_vars(
        {
            "actionConfig": action,
            "siteConfig": {
                "actionConfig": {"alignment_qc": {"core_guardrails": {"max_at_dropout": 3.0}}}
            },
            "regulatory": {},
        }
    )
    window = baked["resolvedConfig__alignment_qc"]
    assert window["core_guardrails"]["max_at_dropout"] == 100.0
