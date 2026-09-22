import json
from pathlib import Path


def test_migrate_guardrail_metric_from_diagnostic_fields():
    from methyl_alignment_qc.utils.guardrail_migration import migrate_guardrail_metric

    legacy = {
        "value": 91.5,
        "threshold": ">= 90",
        "pass": True,
        "diagnose": "Read filtering quality",
        "description": "Percentage of reads passing Illumina PF filter.",
        "meaning": "Lower values often reflect poor cluster quality or run issues.",
        "reason": "PF rate is within expected sequencing quality range.",
    }
    migrated, changed = migrate_guardrail_metric(legacy)

    assert changed is True
    assert migrated["normal_range"] == ">= 90"
    assert "threshold" not in migrated
    assert "message" in migrated
    assert "diagnose" not in migrated
    assert "description" not in migrated
    assert "meaning" not in migrated
    assert "reason" not in migrated


def test_migrate_guardrail_metric_prefers_legacy_note():
    from methyl_alignment_qc.utils.guardrail_migration import migrate_guardrail_metric

    legacy = {
        "value": 25,
        "threshold": ">= 20",
        "pass": True,
        "note": "Lower = more oxidative (G→T) damage",
    }
    migrated, changed = migrate_guardrail_metric(legacy)

    assert changed is True
    assert migrated["normal_range"] == ">= 20"
    assert migrated["message"] == "Lower = more oxidative (G→T) damage"


def test_migrate_guardrails_payload_noop_when_already_migrated():
    from methyl_alignment_qc.utils.guardrail_migration import migrate_guardrails_payload

    payload = {
        "guardrails": {
            "details": {
                "pf_percent": {
                    "value": 97.0,
                    "normal_range": ">= 90",
                    "pass": True,
                    "message": "PF filtering quality.",
                }
            }
        }
    }
    migrated, changed = migrate_guardrails_payload(payload)
    assert changed is False
    assert migrated == payload


def test_migrate_file_apply_overwrites_json(tmp_path: Path):
    from methyl_alignment_qc.utils.guardrail_migration import migrate_file

    sample = {
        "sample_id": "s1",
        "guardrails": {
            "details": {
                "q30_percent": {
                    "value": 88.5,
                    "threshold": ">= 85",
                    "pass": True,
                    "note": "Percentage of bases >= Q30 (after filter)",
                }
            }
        },
    }
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(sample), encoding="utf-8")

    changed, status = migrate_file(path, apply=True, backup=True)
    assert changed is True
    assert status.startswith("MIGRATED")

    migrated = json.loads(path.read_text(encoding="utf-8"))
    metric = migrated["guardrails"]["details"]["q30_percent"]
    assert metric["normal_range"] == ">= 85"
    assert metric["message"] == "Percentage of bases >= Q30 (after filter)"
    assert "threshold" not in metric
    assert "note" not in metric

    backup_path = path.with_suffix(".json.bak")
    assert backup_path.exists()


def test_migrate_guardrails_payload_recurses_nested_headings():
    from methyl_alignment_qc.utils.guardrail_migration import migrate_guardrails_payload

    payload = {
        "guardrails": {
            "details": {
                "bisulfite_conversion": {
                    "conversion_rate_pct": {
                        "value": 99.5,
                        "threshold": ">= 99",
                        "pass": True,
                        "note": "sidecar",
                    }
                }
            }
        }
    }
    migrated, changed = migrate_guardrails_payload(payload)
    assert changed is True
    heading = migrated["guardrails"]["details"]["bisulfite_conversion"]
    assert "message" not in heading
    conv = heading["conversion_rate_pct"]
    assert conv["normal_range"] == ">= 99"
    assert conv["message"] == "sidecar"
    assert "threshold" not in conv
