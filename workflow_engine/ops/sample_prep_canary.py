"""Orchestrate the three-mode SamplePrep real-data canary (DB + workers)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ops._paths import REPO_ROOT, ensure_import_paths


def _python() -> str:
    venv = REPO_ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else sys.executable


def _load_versions(versions_file: Path) -> Dict[str, Any]:
    if not versions_file.is_file():
        return {}
    return json.loads(versions_file.read_text(encoding="utf-8"))


def _sample_prep_version_id(versions: Mapping[str, Any]) -> int:
    vid = (
        versions.get("SamplePrepPipeline", {}).get("workflow_version_id")
        or versions.get("sample_prep_compiled", {}).get("workflow_version_id")
    )
    if not vid:
        raise SystemExit(
            "workflow_version_id missing; run scripts/deploy_workflow_definitions.sh "
            "or scripts/refresh_sample_prep_test_bed.sh"
        )
    return int(vid)


def _write_project(project_path: Path, *, samples_base: Path, output_base: Path, reference_fasta: str) -> None:
    payload = {
        "project_name": "SamplePrep_real_canary",
        "output_base": str(output_base),
        "samples_base_path": str(samples_base),
        "chromosomes": ["1", "21"],
        "contexts": ["CG"],
        "controls": {"label": "healthy", "groups": [{"label": "all", "sample_paths": []}]},
        "diseases": {"label": "cancer", "groups": [{"label": "PCa", "sample_paths": []}]},
    }
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    # Persist reference for planner default path consumers
    sidecar = {
        "referenceFasta": reference_fasta,
    }
    (project_path.parent / "canary_refs.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )


def _preflight(
    *,
    config: Any,
    tier: str,
    modes: Sequence[str],
    require_checksums: bool,
) -> List[str]:
    ensure_import_paths()
    from methyl_utils.testing.sample_prep_canary import verify_fastq_pair

    errors: List[str] = []
    pair = config.subset if tier == "subset" else config.full
    if pair is None:
        errors.append(f"canary config missing '{tier}' FASTQ pair definition")
        return errors

    storage = config.fastq_storage or {}
    base = storage.get("basePath")
    if not base:
        errors.append("fastq_storage.basePath is required for canary preflight")
    else:
        checks = verify_fastq_pair(Path(base), pair, require_checksums=require_checksums)
        for c in checks:
            if not c.ok:
                errors.append(f"{c.name}: {c.detail}")

    pins = config.asset_pins
    if pins and pins.methylgrapher_image_env and "pangenome_wgbs" in modes:
        env_name = pins.methylgrapher_image_env
        if not os.environ.get(env_name):
            errors.append(f"env {env_name} unset (methylGrapher image pin)")

    if os.environ.get("WORKER_STUB_EXTERNAL") in {"1", "true", "TRUE", "yes"}:
        errors.append(
            "WORKER_STUB_EXTERNAL is set; real canary requires WORKER_STUB_EXTERNAL=0 "
            "(or unset) so Parabricks/Giraffe/methylGrapher actually run"
        )
    return errors


def _poll_instance(db: Any, instance_id: int, *, poll: int, timeout: int) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        inst = db.get_workflow_instance(instance_id)
        status = inst.get("status") or inst.get("workflow_status")
        print(f"instance {instance_id} status={status}", flush=True)
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            return str(status)
        time.sleep(poll)
    raise TimeoutError(f"instance {instance_id} not terminal after {timeout}s")


def _list_actions(db: Any, instance_id: int) -> List[str]:
    """Best-effort collect action names from instance tasks."""
    names: List[str] = []
    for attr in ("list_workflow_tasks", "get_workflow_tasks", "list_tasks_for_instance"):
        fn = getattr(db, attr, None)
        if not callable(fn):
            continue
        try:
            tasks = fn(instance_id)
        except TypeError:
            try:
                tasks = fn(workflow_instance_id=instance_id)
            except Exception:
                continue
        except Exception:
            continue
        if not tasks:
            continue
        for t in tasks:
            if not isinstance(t, dict):
                continue
            for key in ("action_name", "actionName", "capability", "name"):
                val = t.get(key)
                if val:
                    names.append(str(val))
                    break
        if names:
            return names
    return names


def run_canary(args: argparse.Namespace) -> int:
    ensure_import_paths()
    from methyl_utils.testing.sample_prep_canary import (
        CheckResult,
        build_qualification_report,
        compare_linear_vs_wgbs,
        resolve_canary_config,
        validate_mode_artifacts,
        write_junit,
        write_markdown_summary,
    )

    config = resolve_canary_config(args.config)
    tier = args.tier
    modes = list(args.modes or config.modes or ["linear", "pangenome", "pangenome_wgbs"])
    for m in modes:
        if m not in {"linear", "pangenome", "pangenome_wgbs"}:
            raise SystemExit(f"unsupported mode: {m}")

    preflight_errors = _preflight(
        config=config,
        tier=tier,
        modes=modes,
        require_checksums=not args.allow_missing_checksums,
    )
    if preflight_errors and not args.skip_preflight and not args.dry_run:
        for err in preflight_errors:
            print(f"preflight FAIL: {err}", file=sys.stderr)
        return 2
    for err in preflight_errors:
        label = "WARN" if args.dry_run or args.skip_preflight else "WARN"
        print(f"preflight {label}: {err}", file=sys.stderr)

    run_root = Path(args.run_root).resolve()
    samples_base = run_root / "samples"
    archive_base = run_root / "archive"
    output_base = run_root / "output"
    project_path = run_root / "project.json"
    report_dir = Path(args.report_dir).resolve() if args.report_dir else run_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    samples_base.mkdir(parents=True, exist_ok=True)
    archive_base.mkdir(parents=True, exist_ok=True)
    output_base.mkdir(parents=True, exist_ok=True)

    reference_fasta = args.reference_fasta or os.environ.get("METHYL_CANARY_REFERENCE_FASTA")
    if not reference_fasta:
        site_path = Path(os.environ.get("METHYL_SITE_CONFIG") or "/work/site/methyl_site.json")
        if site_path.is_file():
            site = json.loads(site_path.read_text(encoding="utf-8"))
            genomes = site.get("genomes") if isinstance(site.get("genomes"), dict) else {}
            linear = genomes.get("linear") if isinstance(genomes.get("linear"), dict) else {}
            reference_fasta = linear.get("fasta") or linear.get("path")
    if not reference_fasta:
        # Fall back to common site layout; planner still validates existence at start.
        reference_fasta = "/work/genomes/homo_sapiens/grch38/fasta/genome.fa"
    _write_project(
        project_path,
        samples_base=samples_base,
        output_base=output_base,
        reference_fasta=str(reference_fasta),
    )

    pair = config.subset if tier == "subset" else config.full
    assert pair is not None
    fastq_prefix = (pair.prefix or "").rstrip("/") + "/"
    storage = dict(config.fastq_storage or {"type": "file", "basePath": str(run_root / "fastq")})

    versions_file = Path(
        args.versions_file
        or os.environ.get("GOLIATH_ENV_DIR", "/work/goliath/env") + "/workflow_versions.json"
    )
    if not versions_file.is_file():
        versions_file = REPO_ROOT / ".smoke" / "workflow_versions.json"
    versions = _load_versions(versions_file)
    sample_prep_vid: Optional[int] = None
    try:
        sample_prep_vid = _sample_prep_version_id(versions)
    except SystemExit:
        if not args.dry_run:
            raise

    base_sample = config.sample_id or "canary-SRR28293403"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta: Dict[str, Any] = {
        "tier": tier,
        "stamp": stamp,
        "reference_fasta": reference_fasta,
        "fastq_prefix": fastq_prefix,
        "fastq_storage": storage,
        "versions_file": str(versions_file),
        "workflow_version_id": sample_prep_vid,
        "preflight_errors": preflight_errors,
        "modes": list(modes),
    }

    if args.dry_run:
        print(json.dumps({"dry_run": True, "modes": modes, "meta": meta}, indent=2))
        return 0
    if sample_prep_vid is None:
        raise SystemExit(
            "workflow_version_id missing; run scripts/deploy_workflow_definitions.sh "
            "or scripts/refresh_sample_prep_test_bed.sh"
        )

    from rest.connection import resolve_connection_config
    from rest.db import open_gateway_db

    # Prefer admin CLI path (same as smoke_sample_prep.sh) for consistent env.
    mode_reports = []
    linear_report = None
    wgbs_report = None
    instance_ids: Dict[str, int] = {}

    for mode in modes:
        sample_id = f"{base_sample}-{mode}-{tier}"
        sample_dir = samples_base / sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        body = {
            "projectPath": str(project_path),
            "workflow_version_id": sample_prep_vid,
            "alignmentMode": mode,
            "libraryProtocol": config.library_protocol or "wgbs_linear",
            "referenceFasta": str(reference_fasta),
            "primaryAnalyte": config.analyte or "buffy_coat",
            "samples": [
                {
                    "sampleId": sample_id,
                    "sampleDir": str(sample_dir),
                    "fastqPrefix": fastq_prefix,
                }
            ],
            "fastqStorage": storage,
            "sampleStorage": {
                "type": "file",
                "basePath": str(archive_base),
            },
            "deleteFastqs": False,
        }
        if config.directional is not None and mode == "pangenome_wgbs":
            body["actionConfig"] = {
                "methylgrapher_wgbs": {"directional": bool(config.directional)}
            }

        print(json.dumps({"starting_mode": mode, "sample_id": sample_id}, indent=2), flush=True)
        proc = subprocess.run(
            [_python(), "-m", "admin.study_start", "sample-prep-start", "-"],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT / "workflow_engine"),
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "workflow_engine")},
        )
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            raise SystemExit(f"sample-prep-start failed for mode={mode}")
        started = json.loads(proc.stdout)
        instance_id = int(started["instance_id"])
        instance_ids[mode] = instance_id

        db_cfg = resolve_connection_config()
        db = open_gateway_db(db_cfg)
        try:
            status = _poll_instance(db, instance_id, poll=args.poll_seconds, timeout=args.timeout)
            actions = _list_actions(db, instance_id)
        finally:
            db.close()
        if status != "COMPLETED":
            print(f"mode={mode} instance={instance_id} status={status}", file=sys.stderr)
            # Still validate whatever artifacts exist for the report.
        report = validate_mode_artifacts(
            mode=mode,
            sample_id=sample_id,
            sample_dir=sample_dir,
            thresholds=config.thresholds,
            observed_actions=actions or None,
        )
        archive_manifest = archive_base / sample_id / "archive_manifest.json"
        report.checks.append(
            CheckResult(
                "archive_manifest_present",
                archive_manifest.is_file(),
                str(archive_manifest),
            )
        )
        report.checks.append(
            CheckResult(
                "workflow_completed",
                status == "COMPLETED",
                f"status={status} instance_id={instance_id}",
            )
        )
        mode_reports.append(report)
        if mode == "linear":
            linear_report = report
        if mode == "pangenome_wgbs":
            wgbs_report = report

    comparison = []
    if linear_report is not None and wgbs_report is not None:
        comparison = compare_linear_vs_wgbs(
            linear_report, wgbs_report, thresholds=config.thresholds
        )

    meta["instance_ids"] = instance_ids
    qualification = build_qualification_report(
        config=config,
        tier=tier,
        mode_reports=mode_reports,
        comparison_checks=comparison,
        meta=meta,
    )
    json_path = report_dir / f"sample_prep_canary_{tier}_{stamp}.json"
    junit_path = report_dir / f"sample_prep_canary_{tier}_{stamp}.junit.xml"
    md_path = report_dir / f"sample_prep_canary_{tier}_{stamp}.md"
    latest_json = report_dir / f"sample_prep_canary_{tier}_latest.json"
    json_path.write_text(json.dumps(qualification, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(json_path, latest_json)
    write_junit(qualification, junit_path)
    write_markdown_summary(qualification, md_path)
    print(json.dumps({"report": str(json_path), "junit": str(junit_path), "markdown": str(md_path), "overall_pass": qualification["overall_pass"]}, indent=2))
    return 0 if qualification["overall_pass"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run SamplePrep real-data canary (3 alignment modes)")
    p.add_argument("--tier", choices=("subset", "full"), default="subset")
    p.add_argument(
        "--modes",
        nargs="+",
        choices=("linear", "pangenome", "pangenome_wgbs"),
        default=None,
    )
    p.add_argument("--config", default=None, help="Path to SamplePrepCanaryConfig JSON")
    p.add_argument(
        "--run-root",
        default=str(REPO_ROOT / ".smoke" / "sample_prep_canary"),
        help="Working root for samples/archive/reports",
    )
    p.add_argument("--report-dir", default=None)
    p.add_argument("--versions-file", default=None)
    p.add_argument("--reference-fasta", default=None)
    p.add_argument("--poll-seconds", type=int, default=30)
    p.add_argument("--timeout", type=int, default=14400, help="Per-mode timeout seconds")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-preflight", action="store_true")
    p.add_argument(
        "--allow-missing-checksums",
        action="store_true",
        help="Allow canary config without provisioned SHA-256 pins (not for qualification).",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return run_canary(args)


if __name__ == "__main__":
    raise SystemExit(main())
