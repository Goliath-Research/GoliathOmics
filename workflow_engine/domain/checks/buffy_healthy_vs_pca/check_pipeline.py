#!/usr/bin/env python3
"""Validate and optionally deploy the Buffy healthy vs PCa DomainProgram check bundle."""

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

PROJECT_NAME = "project_Buffy_healthy_vs_PCa.json"
LEGACY_ALIAS = "Buffy_healthy_vs_PCa.json"
PROGRAM_NAME = "data_driven.program.json"
FIXTURES = REPO_ROOT / "workflow_engine" / "domain" / "fixtures"


def _ensure_import_paths() -> None:
    for rel in (
        "workflow_engine/domain",
        "workflow_engine/contract",
        "workers",
        "packages/methyldomain",
    ):
        p = REPO_ROOT / rel
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def load_bundle_paths() -> dict[str, Path]:
    return {
        "project": CONFIGS / PROJECT_NAME,
        "program": FIXTURES / PROGRAM_NAME,
        "instance_context": INSTANCE / "context.json",
        "healthy_csv": DATA / "healthy_b.csv",
        "pca_csv": DATA / "pca_b.csv",
    }


def validate_project(project_path: Path) -> dict:
    from methyl_utils import load_project

    project = load_project(str(project_path))
    comparisons = project.get_comparisons()
    if not comparisons:
        raise ValueError("project has no resolved comparisons")
    raw = json.loads(project_path.read_text(encoding="utf-8")).get("comparisons")
    if isinstance(raw, str):
        raise ValueError(
            f"comparisons must be a JSON array for collection bindings, not shorthand {raw!r}"
        )
    chromosomes = list(project.chromosomes or [])
    contexts = list(getattr(project, "contexts", None) or ["CG"])
    return {
        "project_name": project.project_name,
        "comparisons": len(comparisons),
        "chromosomes": len(chromosomes),
        "contexts": len(contexts),
        "detection_dir": project.get_detection_output_dir(
            comparisons[0].control_group, comparisons[0].disease_group
        ),
        "mapper_dir": project.get_mapper_output_dir(
            comparisons[0].control_group, comparisons[0].disease_group
        ),
    }


def compile_program(program_path: Path, project_info: dict) -> tuple[dict, dict]:
    _ensure_import_paths()
    from compiler import compile_domain_program_file

    result = compile_domain_program_file(program_path, enrich_context=True)
    wf = result.workflow
    action_nodes = [n for n in wf.nodes if n.node_type == "ACTION"]
    foreach_nodes = [n for n in wf.nodes if n.node_type == "FOREACH"]
    bindings = [b.model_dump() for b in wf.collection_bindings]
    per_chr_ctx = (
        project_info["chromosomes"]
        * project_info["contexts"]
        * project_info["comparisons"]
    )
    expected_leaf_tasks = per_chr_ctx * 3 + 2
    summary = {
        "workflow_name": wf.name,
        "node_count": len(wf.nodes),
        "action_nodes": len(action_nodes),
        "foreach_nodes": len(foreach_nodes),
        "collection_bindings": bindings,
        "context_json": result.context_json,
        "expected_leaf_tasks": expected_leaf_tasks,
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
        paths["healthy_csv"]: WORK_DATA / "healthy_b.csv",
        paths["pca_csv"]: WORK_DATA / "pca_b.csv",
    }
    for src, dst in targets.items():
        if dst.exists() and not force:
            print(f"skip (exists): {dst}")
            continue
        shutil.copy2(src, dst)
        print(f"installed: {dst}")

    legacy = WORK_CONFIGS / LEGACY_ALIAS
    if legacy.exists() or legacy.is_symlink():
        legacy.unlink()
    legacy.symlink_to(WORK_CONFIGS / PROJECT_NAME)
    print(f"symlink: {legacy} -> {PROJECT_NAME}")


def write_compiled_spec(out_dir: Path, spec: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "compiled_workflow.json"
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return path


def deploy_to_postgres(spec: dict, dsn: str) -> int:
    spec_json = json.dumps(spec).replace("'", "''")
    ctx_json = json.dumps(
        json.loads((INSTANCE / "context.json").read_text(encoding="utf-8"))
    ).replace("'", "''")
    sql = f"""
SELECT wf.wf_repo_create_workflow_graph('{spec_json}'::jsonb)::text AS created;
"""
    proc = subprocess.run(
        ["psql", "-q", dsn, "-t", "-A", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "methyl")},
    )
    created = json.loads(proc.stdout.strip())
    version_id = created["workflow_version_id"]
    inst_sql = (
        f"SELECT wf.wf_repo_create_workflow_instance({version_id}, '{ctx_json}'::jsonb);"
    )
    inst_proc = subprocess.run(
        ["psql", "-q", dsn, "-t", "-A", "-c", inst_sql],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "methyl")},
    )
    instance_id = int(inst_proc.stdout.strip())
    subprocess.run(
        ["psql", "-q", dsn, "-c", f"CALL wf.sp_start_workflow_instance({instance_id});"],
        check=True,
        env={**os.environ, "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "methyl")},
    )
    print(f"deployed workflow_version_id={version_id} instance_id={instance_id}")
    return instance_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=Path,
        default=CONFIGS / PROJECT_NAME,
        help="Project JSON to validate",
    )
    parser.add_argument(
        "--program",
        type=Path,
        default=CONFIGS / PROGRAM_NAME,
        help="DomainProgram JSON to compile",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Copy project template and sample CSVs to /work (not DomainPrograms)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing /work files",
    )
    parser.add_argument(
        "--write-spec",
        type=Path,
        default=CHECK_ROOT / "compiled",
        help="Write compiled WorkflowDefinitionSpec JSON",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy compiled workflow and start instance (needs PostgreSQL wf schema)",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "WF_PG_DSN",
            "postgresql://postgres:methyl@localhost:5432/methylpipeline_parity",
        ),
    )
    args = parser.parse_args()

    paths = {
        "project": args.project,
        "program": args.program,
        "instance_context": INSTANCE / "context.json",
        "healthy_csv": DATA / "healthy_b.csv",
        "pca_csv": DATA / "pca_b.csv",
    }
    for label, path in paths.items():
        if not path.is_file():
            print(f"missing {label}: {path}", file=sys.stderr)
            return 1

    project_info = validate_project(paths["project"])
    print("project:", json.dumps(project_info, indent=2))

    summary, spec = compile_program(paths["program"], project_info)
    print("compile:", json.dumps(summary, indent=2))

    _ensure_import_paths()
    from workflow_context import resolve_input_json_from_template, validate_resolved_input_json

    scope = {
        "projectPath": summary["context_json"]["projectPath"],
        "centroid1Dir": summary["context_json"].get("centroid1Dir", "/work/out/centroid1"),
        "centroid2Dir": "/work/out/centroid2",
        "detectOutDir": "/work/out/detect",
        "chromosome": "21",
        "context": "CG",
        "control_group": "all",
        "disease_group": "PCa",
        "label": "PCa",
        "comparison": "PCa",
        "discoveryCsv": "/work/out/detect/dmps-21-discovery.csv",
        "runDmpSelection": False,
        "outputDir": "/work/out/detect",
        "group": "PCa",
    }
    template_errors = []
    for node in spec.get("nodes") or []:
        if node.get("node_type") != "ACTION" or not node.get("input_template"):
            continue
        resolved = resolve_input_json_from_template(node["input_template"], scope)
        errs = validate_resolved_input_json(resolved, str(node.get("action_name")))
        if errs:
            template_errors.append(f"{node.get('node_key')}: {errs}")
    if template_errors:
        print("template validation errors:", template_errors, file=sys.stderr)
        return 1
    print("template validation: all ACTION templates resolve for smoke scope")

    spec_path = write_compiled_spec(args.write_spec, spec)
    print(f"wrote compiled spec: {spec_path}")

    if args.install:
        install_to_work(force=args.force)

    if args.deploy:
        deploy_to_postgres(spec, args.dsn)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
