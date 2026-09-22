"""Scaffold a disease-agnostic SaMD study under /work/projects/<study-id>/."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _slug_study_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", name.strip())
    return cleaned.strip("_") or "Study"


def _empty_partitions() -> Dict[str, Any]:
    return {
        "development_train": [],
        "internal_validation": [],
        "locked_test": [],
        "pivotal_validation": [],
        "post_market_monitoring": [],
        "independence_keys": ["sample_id", "patient_id", "site_id", "batch"],
    }


def build_manifest(
    *,
    project_name: str,
    study_id: str,
    output_root: Path,
    analyte: Optional[str],
    stages: Optional[int],
    intended_use: str,
    modality: str = "methylation",
) -> Dict[str, Any]:
    data_dir = output_root / study_id / "data"
    healthy_csv = str(data_dir / "healthy.csv")
    controls = {
        "label": "healthy",
        "groups": [{"label": "all", "sample_paths": [healthy_csv]}],
    }
    if stages and stages > 0:
        stage_entries = []
        for i in range(1, stages + 1):
            stage_entries.append(
                {
                    "label": f"stage{i}",
                    "description": f"Disease stage {i}",
                    "sample_paths": [str(data_dir / f"stage_{i}.csv")],
                }
            )
        diseases = {
            "label": "disease",
            "groups": [{"label": "disease", "stages": stage_entries}],
        }
        progression_labels = [f"disease_stage{i}" for i in range(1, stages + 1)]
        progression = {
            "progression_order": "explicit",
            "progression_labels": progression_labels,
        }
    else:
        diseases = {
            "label": "disease",
            "groups": [
                {
                    "label": "disease",
                    "sample_paths": [str(data_dir / "disease.csv")],
                }
            ],
        }
        progression = {}

    manifest: Dict[str, Any] = {
        "project_name": project_name,
        "output_base": str(output_root / study_id),
        "samples_base_path": "/work/samples",
        "controls": controls,
        "diseases": diseases,
        "comparisons": "control_vs_each_disease",
        "chromosomes": [str(c) for c in range(1, 23)] + ["X", "Y"],
        "contexts": ["CG"],
        "regulatory": {
            "stage": "expanded_development",
            "intended_use_summary": intended_use,
            "primary_modality": modality,
            "allow_clinical_performance_claims": False,
            "claim_boundary": "Development evidence only until pivotal_validation stage.",
        },
        "validation_partitions": _empty_partitions(),
    }
    # primary_analyte is study-owned and methylation-pack-only.
    if analyte:
        manifest["regulatory"]["primary_analyte"] = analyte
    manifest.update(progression)
    return manifest


def _csv_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("sample\n", encoding="utf-8")


def write_study(
    *,
    study_id: str,
    name: str,
    analyte: Optional[str],
    stages: Optional[int],
    output_root: Path,
    intended_use: str,
    force: bool,
    modality: str = "methylation",
) -> Path:
    project_name = _slug_study_name(name)
    study_root = output_root / study_id
    configs = study_root / "configs"
    data = study_root / "data"
    if study_root.exists() and not force:
        raise FileExistsError(
            f"{study_root} already exists (pass --force to overwrite manifest/README stubs)"
        )
    configs.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    _csv_stub(data / "healthy.csv")
    if stages and stages > 0:
        for i in range(1, stages + 1):
            _csv_stub(data / f"stage_{i}.csv")
    else:
        _csv_stub(data / "disease.csv")

    manifest = build_manifest(
        project_name=project_name,
        study_id=study_id,
        output_root=output_root,
        analyte=analyte,
        stages=stages,
        intended_use=intended_use,
        modality=modality,
    )
    manifest_path = configs / f"project_{project_name}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # DB-first / registry: also upsert into cfg store then ensure materialize path matches.
    try:
        from cfg.store import FileConfigStore

        store_dir = Path(
            __import__("os").environ.get(
                "METHYL_CFG_STORE",
                str(Path("/work/goliath/cfg-store")),
            )
        )
        store = FileConfigStore(store_dir)
        store.upsert(
            "study",
            project_name,
            manifest,
            status="published",
            extra={"studyId": study_id},
        )
    except Exception as exc:  # pragma: no cover - best-effort registry mirror
        print(f"warning: cfg study upsert skipped: {exc}", file=sys.stderr)

    readme = configs / "README.md"
    readme.write_text(
        f"""# Study `{project_name}` (SaMD ladder)

Scaffolded by `methyl-study-init`. Fill `../data/*.csv`, then assign patient-disjoint
holdouts in `validation_partitions` inside `{manifest_path.name}`.

## Profile ladder

| Tier | Profile | Required partitions |
|------|---------|---------------------|
| Research | `samd_research` | `locked_test` recommended |
| Enrichment | `samd_holdout_enrichment` | **non-empty** `locked_test` |
| Pivotal | `samd_pivotal` | **non-empty** `pivotal_validation` |

See `docs/usage/18-samd-study-lifecycle.md`.

## Validate

```bash
methyl-study-validate-manifest --project {manifest_path} --profile samd_holdout_enrichment
```

## Example run (research, binary)

```bash
methyl-workflow-run \\
  --program workflow_engine/domain/fixtures/samd_research.program.json \\
  --context-file workflow_engine/domain/profiles/samd_research.profile.json \\
  --context '{{"projectPath":"{manifest_path}","pipelineProfile":"samd_research"}}'
```
""",
        encoding="utf-8",
    )
    return manifest_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a SaMD-aligned study layout under /work/projects/<study-id>/."
    )
    parser.add_argument("--study-id", required=True, help="Directory name under output root")
    parser.add_argument("--name", required=True, help="Study / project_name (e.g. Healthy_vs_Disease)")
    parser.add_argument(
        "--analyte",
        default=None,
        help=(
            "regulatory.primary_analyte (study-owned). "
            "Required for --modality methylation (cfdna|buffy_coat|tissue|plant_tissue). "
            "Omit for rnaseq/proteomics."
        ),
    )
    parser.add_argument(
        "--modality",
        default="methylation",
        choices=["methylation", "rnaseq", "proteomics"],
        help="regulatory.primary_modality: omics process pack (default: methylation)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("/work/projects"),
        help="Parent directory for studies (default: /work/projects)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--binary", action="store_true", help="Healthy vs one disease group")
    group.add_argument(
        "--stages",
        type=int,
        metavar="N",
        help="Healthy vs N disease stages under one parent group",
    )
    parser.add_argument(
        "--intended-use",
        default="Research-use methylation classifier for healthy vs disease discrimination.",
        help="regulatory.intended_use_summary",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite manifest/README if the study directory already exists",
    )
    parser.add_argument(
        "--cfg-store",
        type=Path,
        default=None,
        help="cfg FileConfigStore dir (default: METHYL_CFG_STORE or /work/goliath/cfg-store)",
    )
    args = parser.parse_args(argv)

    stages = None if args.binary else int(args.stages)
    if stages is not None and stages < 1:
        parser.error("--stages must be >= 1")

    analyte = args.analyte
    if args.modality == "methylation":
        if not analyte:
            analyte = "buffy_coat"
            print(
                "warning: --analyte omitted for methylation; defaulting to buffy_coat. "
                "Set --analyte cfdna|buffy_coat|tissue|plant_tissue explicitly for production.",
                file=sys.stderr,
            )
    else:
        # Non-methyl packs must not inherit a methylation analyte default.
        analyte = analyte  # may be None; omitted from regulatory

    if args.cfg_store is not None:
        import os

        os.environ["METHYL_CFG_STORE"] = str(args.cfg_store.resolve())

    try:
        path = write_study(
            study_id=args.study_id,
            name=args.name,
            analyte=analyte,
            stages=stages,
            output_root=args.output_root.resolve(),
            intended_use=args.intended_use,
            force=args.force,
            modality=args.modality,
        )
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote study manifest: {path}")
    print(f"Next: fill cohort CSVs, assign locked_test, see {path.parent / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
