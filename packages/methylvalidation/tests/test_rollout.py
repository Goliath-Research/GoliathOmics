import json

from methyl_validation.rollout import evaluate_dual_run, write_rollout_report


def test_evaluate_dual_run_promote_with_improved_reliability(tmp_path):
    baseline = {
        "metrics": {
            "balanced_accuracy": {"mean": 0.88},
            "macro_f1": {"mean": 0.86},
            "nll": {"mean": 0.40},
            "brier_score": {"mean": 0.18},
            "ece": {"mean": 0.10},
        }
    }
    candidate = {
        "metrics": {
            "balanced_accuracy": {"mean": 0.879},
            "macro_f1": {"mean": 0.858},
            "nll": {"mean": 0.35},
            "brier_score": {"mean": 0.16},
            "ece": {"mean": 0.09},
        }
    }
    b_path = tmp_path / "baseline.json"
    c_path = tmp_path / "candidate.json"
    b_path.write_text(json.dumps(baseline), encoding="utf-8")
    c_path.write_text(json.dumps(candidate), encoding="utf-8")

    report = evaluate_dual_run(
        baseline_summary_path=b_path,
        candidate_summary_path=c_path,
    )
    assert report["recommendation"] == "promote"
    out = write_rollout_report(report, tmp_path / "rollout_report.json")
    assert out.is_file()

