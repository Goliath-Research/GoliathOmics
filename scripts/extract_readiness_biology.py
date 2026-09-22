from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _pick(src: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {k: src.get(k) for k in keys if k in src}


def main() -> None:
    readiness_path = Path("/work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/readiness/readiness.json")
    out_path = Path("/work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/model_family_analysis/reports/biological_coherence_baseline.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.loads(readiness_path.read_text(encoding="utf-8"))
    progression = payload.get("progression", {}) or {}
    canonical = progression.get("module_trajectory", {}) or {}
    variant = progression.get("module_trajectory_variant", {}) or {}
    detailed = progression.get("module_trajectory_detailed", {}) or {}
    verdict = payload.get("verdict", {}) or {}

    out = {
        "readiness_verdict": _pick(verdict, ["overall", "enricher", "stability", "freeze", "progression"]),
        "canonical_track": _pick(
            canonical,
            [
                "n_unique_entities",
                "entities_all_stages",
                "median_abs_pearson_stage_vs_score",
                "median_abs_spearman_stage_vs_score",
                "fraction_monotone_up",
                "fraction_monotone_down",
                "fraction_mostly_monotone_up",
                "fraction_mostly_monotone_down",
            ],
        ),
        "variant_track": _pick(
            variant,
            [
                "n_unique_entities",
                "entities_all_stages",
                "median_abs_pearson_stage_vs_score",
                "median_abs_spearman_stage_vs_score",
                "fraction_monotone_up",
                "fraction_monotone_down",
                "fraction_mostly_monotone_up",
                "fraction_mostly_monotone_down",
            ],
        ),
        "detailed_track": _pick(
            detailed,
            [
                "n_unique_entities",
                "entities_all_stages",
                "median_abs_pearson_stage_vs_score",
                "median_abs_spearman_stage_vs_score",
                "fraction_monotone_up",
                "fraction_monotone_down",
                "fraction_mostly_monotone_up",
                "fraction_mostly_monotone_down",
            ],
        ),
    }
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
