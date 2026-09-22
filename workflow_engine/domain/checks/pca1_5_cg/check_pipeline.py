#!/usr/bin/env python3
"""Validate and optionally deploy the PCa1-5 DomainProgram check bundle."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CHECK_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CHECK_ROOT.parents[3]
CONFIGS = CHECK_ROOT / "configs"
INSTANCE = CHECK_ROOT / "instance"
DATA = CHECK_ROOT / "data"

WORK_ROOT = Path("/work/projects/prostate-cancer")
WORK_CONFIGS = WORK_ROOT / "configs"
WORK_DATA = WORK_ROOT / "data"

PROJECT_NAME = "project_Healthy_vs_PCa1-5-CG.json"
PROJECT_SMOKE_NAME = "project_Healthy_vs_PCa1-5-CG_smoke.json"
FIXTURES = REPO_ROOT / "workflow_engine" / "domain" / "fixtures"
PROGRAM_MC = "mc_stability_staged.program.json"
PROGRAM_MC_SMOKE = "mc_stability_smoke.program.json"
PROGRAM_LIFECYCLE = "study_validation_lifecycle.program.json"


def _ensure_import_paths() -> None:
    for rel in (
        "workflow_engine/domain",
        "workflow_engine/contract",
        "workers",
        "packages/methyldomain",
        "packages/methylvalidation",
    ):
        p = REPO_ROOT / rel
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def load_bundle_paths() -> dict[str, Path]:
    return {
        "project": CONFIGS / PROJECT_NAME,
        "project_smoke": CONFIGS / PROJECT_SMOKE_NAME,
        "program_mc": FIXTURES / PROGRAM_MC,
        "program_mc_smoke": FIXTURES / PROGRAM_MC_SMOKE,
        "program_lifecycle": FIXTURES / PROGRAM_LIFECYCLE,
        "instance_context": INSTANCE / "context.json",
        "instance_context_smoke": INSTANCE / "context_smoke.json",
        "healthy_csv": DATA / "pca_h.csv",
        "pca1_csv": DATA / "pca1.csv",
    }


def validate_project(project_path: Path) -> dict:
    from methyl_utils import load_project

    project = load_project(str(project_path))
    comparisons = project.get_comparisons()
    if not comparisons:
        raise ValueError("project has no resolved comparisons")
    raw = json.loads(project_path.read_text(encoding="utf-8"))
    raw_comparisons = raw.get("comparisons")
    if isinstance(raw_comparisons, str):
        comparisons_note = f"resolved from shorthand {raw_comparisons!r}"
    elif isinstance(raw_comparisons, list):
        comparisons_note = f"{len(raw_comparisons)} explicit entries"
    else:
        comparisons_note = "resolved"
    groups = project.get_resolved_groups()
    chromosomes = list(project.chromosomes or [])
    contexts = list(getattr(project, "contexts", None) or ["CG"])
    return {
        "project_name": project.project_name,
        "comparisons": len(comparisons),
        "comparisons_note": comparisons_note,
        "groups": len(groups),
        "chromosomes": len(chromosomes),
        "contexts": len(contexts),
        "layout": "hierarchical_multiclass",
    }


def compile_program(program_path: Path, project_path: Path) -> tuple[dict, dict]:
    _ensure_import_paths()
    from compiler import compile_domain_program_file
    from workflow_context import enrich_instance_context

    result = compile_domain_program_file(program_path, enrich_context=False)
    wf = result.workflow
    action_nodes = [n for n in wf.nodes if n.node_type == "ACTION"]
    foreach_nodes = [n for n in wf.nodes if n.node_type == "FOREACH"]
    bindings = [b.model_dump() for b in wf.collection_bindings]
    context_json: dict = {"projectPath": str(project_path.resolve())}
    try:
        context_json = enrich_instance_context(context_json)
    except (FileNotFoundError, ValueError):
        pass
    summary = {
        "workflow_name": wf.name,
        "node_count": len(wf.nodes),
        "action_nodes": len(action_nodes),
        "foreach_nodes": len(foreach_nodes),
        "collection_bindings": bindings,
        "context_json": context_json,
        "actions": [n.action_name for n in action_nodes if n.action_name],
    }
    spec = wf.model_dump(mode="json")
    return summary, spec


def install_to_work(force: bool) -> None:
    paths = load_bundle_paths()
    WORK_CONFIGS.mkdir(parents=True, exist_ok=True)
    WORK_DATA.mkdir(parents=True, exist_ok=True)

    # Study manifests and sample lists only — DomainPrograms stay in the repo.
    targets = {
        paths["project"]: WORK_CONFIGS / PROJECT_NAME,
        paths["project_smoke"]: WORK_CONFIGS / PROJECT_SMOKE_NAME,
        paths["healthy_csv"]: WORK_DATA / "PCaH.csv",
        paths["pca1_csv"]: WORK_DATA / "PCa1.csv",
    }
    for src, dst in targets.items():
        if dst.exists() and not force:
            print(f"skip (exists): {dst}")
            continue
        shutil.copy2(src, dst)
        print(f"installed: {dst}")


def write_compiled_spec(out_dir: Path, spec: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "compiled_workflow.json"
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=CONFIGS / PROJECT_NAME)
    parser.add_argument("--program", type=Path, default=FIXTURES / PROGRAM_MC)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--write-spec", type=Path, default=CHECK_ROOT / "compiled")
    args = parser.parse_args()

    paths = load_bundle_paths()
    for label, path in paths.items():
        if label.startswith("program"):
            if not path.is_file():
                print(f"missing {label}: {path}", file=sys.stderr)
                return 1
        elif not path.is_file():
            print(f"warn: missing {label}: {path}", file=sys.stderr)

    try:
        project_info = validate_project(args.project)
        print("project:", json.dumps(project_info, indent=2))
    except FileNotFoundError as exc:
        # Compile-only environments may lack /work sample CSVs.
        print(f"project validate skipped (missing samples): {exc}", file=sys.stderr)
        project_info = {"project_name": args.project.name}

    summary, spec = compile_program(args.program, args.project)
    print("compile:", json.dumps(summary, indent=2))

    binding_vars = {b["scope_var"] for b in summary["collection_bindings"]}
    if "groups" not in summary["context_json"] and "groups" not in binding_vars:
        print(
            "warn: groups not in context (enrich skipped or unavailable); continuing compile check",
            file=sys.stderr,
        )

    _ensure_import_paths()
    from workflow_context import resolve_input_json_from_template, validate_resolved_input_json

    scope = {
        **summary["context_json"],
        "projectPath": summary["context_json"]["projectPath"],
        "centroid1Dir": "/work/out/centroid1",
        "centroid2Dir": "/work/out/centroid2",
        "detectOutDir": "/work/out/detect",
        "chromosome": "21",
        "context": "CG",
        "group": "all",
        "label": "all",
        "comparison": "PCa",
        "discoveryCsv": "/work/out/detect/dmps-21-discovery.csv",
        "runDir": "/work/out/monte_carlo_runs/run_0001",
        "iteration": {
            "projectPath": summary["context_json"]["projectPath"],
            "runDir": "/work/out/monte_carlo_runs/run_0001",
        },
        "runDmpSelection": True,
        "runBiomarkerFilter": True,
        "runGeneFeaturecuts": True,
        "runGeneFeatureSelect": False,
        "maxGenes": 500,
        "maxDmps": 500,
        "mapperDir": "/work/out/mapper",
        "maxFeatures": 200,
        "targetBalancedAccuracy": 0.9,
        "comparison": "PCa_PCa1",
        "stepOverride": {
            "base_config": {
                "add_samples": ["/work/samples/S1"],
                "remove_samples": [],
            }
        },
        "addSamples": ["/work/samples/S1"],
        "removeSamples": [],
        "group": {
            "label": "all",
            "centroidDir": "/work/out/centroids/all",
            "addSamples": ["/work/samples/S1"],
            "removeSamples": [],
        },
        "fixedDmpPanel": "/work/out/stable_dmps_genomewide.csv",
        "centroidDir": "/work/out/centroids/all",
        "outputDir": "/work/out/centroids/all",
        "featureIterations": 30,
        "qualityIterations": 0,
    }
    template_errors = []
    for node in spec.get("nodes") or []:
        if node.get("node_type") != "ACTION" or not node.get("input_template"):
            continue
        action_name = str(node.get("action_name") or "")
        if action_name.startswith("validation."):
            continue
        try:
            resolved = resolve_input_json_from_template(node["input_template"], scope)
        except KeyError:
            continue
        errs = validate_resolved_input_json(resolved, str(node.get("action_name")))
        if errs:
            template_errors.append(f"{node.get('node_key')}: {errs}")
    if template_errors:
        print("template validation errors:", template_errors, file=sys.stderr)
        return 1
    print("template validation: all ACTION templates resolve for smoke scope")

    spec_path = write_compiled_spec(args.write_spec, spec)
    print(f"wrote compiled spec: {spec_path}")

    lifecycle_program = FIXTURES / PROGRAM_LIFECYCLE
    if lifecycle_program.is_file():
        lifecycle_summary, lifecycle_spec = compile_program(lifecycle_program, args.project)
        lifecycle_dir = CHECK_ROOT / "compiled" / "study_validation_lifecycle"
        lifecycle_path = write_compiled_spec(lifecycle_dir, lifecycle_spec)
        print("lifecycle compile:", json.dumps(lifecycle_summary, indent=2))
        print(f"wrote lifecycle compiled spec: {lifecycle_path}")
        output_bindings = lifecycle_spec.get("output_bindings") or []
        bound_vars = {b.get("var_name") for b in output_bindings}
        assert bound_vars >= {"iterations", "fixedDmpPanel", "selectedBackend"}, (
            f"missing lifecycle output bindings: {bound_vars}"
        )

    if args.install:
        install_to_work(force=args.force)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
