"""Cox / KM survival backend smoke."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from methyl_validation.survival_backend import (
    cox_ph_fit,
    harrell_c,
    kaplan_meier,
    run_survival_model,
)


def test_cox_separates_risk():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(40, 2))
    time = np.clip(20 - x[:, 0] * 4 + rng.normal(scale=0.5, size=40), 0.5, None)
    event = np.ones(40, dtype=int)
    beta, risk = cox_ph_fit(x, time, event)
    assert beta.shape == (2,)
    c = harrell_c(risk, time, event)
    assert c > 0.6
    km = kaplan_meier(time, event)
    assert not km.empty


def test_run_survival_with_mhl_and_sidecar(tmp_path: Path):
    from types import SimpleNamespace

    project = tmp_path / "project.json"
    samples = tmp_path / "samples"
    samples.mkdir()
    for i in range(8):
        (samples / f"s{i}").mkdir()
    surv = tmp_path / "survival.csv"
    rows = [{"sample_id": f"s{i}", "time": 5 + i, "event": int(i % 2 == 0), "psa": float(i)} for i in range(8)]
    pd.DataFrame(rows).to_csv(surv, index=False)
    mhl = tmp_path / "mhl_matrix.csv"
    pd.DataFrame(
        {"sample_id": [f"s{i}" for i in range(8)], "blk1": np.linspace(0.1, 0.8, 8)}
    ).to_csv(mhl, index=False)
    payload = {
        "project_name": "mhl_surv",
        "output_base": str(tmp_path),
        "group1": {"label": "a", "sample_paths": [str(samples / f"s{i}") for i in range(4)]},
        "group2": {"label": "b", "sample_paths": [str(samples / f"s{i}") for i in range(4, 8)]},
        "survival_path": str(surv),
        "chromosomes": ["21"],
    }
    project.write_text(json.dumps(payload), encoding="utf-8")
    params = SimpleNamespace(
        mhl_matrix_path=str(mhl),
        clinical_columns=["psa"],
        time_auc_horizons=[6, 10],
        write_nomogram=True,
        nested_lrt=False,
    )
    cfg = SimpleNamespace(get_backend_params=lambda _name: params)
    out = tmp_path / "model"
    metrics = run_survival_model(project_json=project, output_dir=out, config=cfg)
    assert metrics["backend"] == "cox"
    assert metrics["n_samples"] == 8
    assert (out / "survival_metrics.json").is_file()
    assert (out / "nomogram.json").is_file()
    assert "concordance" in metrics
