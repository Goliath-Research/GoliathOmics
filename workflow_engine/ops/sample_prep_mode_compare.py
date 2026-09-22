"""Orchestrate linear vs pangenome_wgbs SamplePrep comparison (DB + workers).

Experiment-only mode trees under each sample root; FASTQs stay at the sample
root (lab/QNAP layout). Does not set sampleStorage (no QNAP archive).

Multi-arm before/after bakeoffs (Clara / Mojo / vg coexisting ``align.*`` dirs +
``/work/samples/_comparisons/<stamp>`` reports) live in
``methyl_utils.testing.sample_prep_mode_compare``
(``ensure_comparison_arms``, ``write_comparison_arms_report``) and
``scripts/comparison_arms_report.py``. See ``docs/architecture/sample-prep-tooling.md``.
"""

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


DEFAULT_SAMPLES = (
    {
        "sampleId": "DPLST-051425-111148",
        "primaryAnalyte": "cfdna",
        "projectPath": "/work/projects/prostate-cancer/configs/project_Plasma_healthy_vs_PCa.json",
        "pipelineProcedure": "cfdna_wgbs_plasma",
        "libraryProtocol": "wgbs_linear",
    },
    {
        "sampleId": "DBCST-051425-111148",
        "primaryAnalyte": "buffy_coat",
        "projectPath": "/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json",
        "pipelineProcedure": "buffy_wgbs_linear_gene_fc",
        "libraryProtocol": "wgbs_linear",
    },
)


def _python() -> str:
    venv = REPO_ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else sys.executable


def _resolve_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if p.is_file() or p.is_dir():
        return p.resolve()
    candidate = (REPO_ROOT / p).resolve()
    if candidate.exists():
        return candidate
    return p.resolve()


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object in {path}")
    return data


def _load_versions(versions_file: Path) -> Dict[str, Any]:
    if not versions_file.is_file():
        return {}
    return json.loads(versions_file.read_text(encoding="utf-8"))


def _sample_prep_version_id(versions: Mapping[str, Any]) -> Optional[int]:
    vid = (
        versions.get("SamplePrepPipeline", {}).get("workflow_version_id")
        or versions.get("sample_prep_compiled", {}).get("workflow_version_id")
    )
    return int(vid) if vid else None


def _load_site() -> Dict[str, Any]:
    site_path = Path(os.environ.get("METHYL_SITE_CONFIG") or "/work/site/methyl_site.json")
    if site_path.is_file():
        site = json.loads(site_path.read_text(encoding="utf-8"))
        return site if isinstance(site, dict) else {}
    return {}


def _reference_fasta(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    env = os.environ.get("METHYL_COMPARE_REFERENCE_FASTA")
    if env:
        return env
    site = _load_site()
    genomes = site.get("genomes") if isinstance(site.get("genomes"), dict) else {}
    linear = genomes.get("linear") if isinstance(genomes.get("linear"), dict) else {}
    ref = linear.get("fasta") or linear.get("path")
    if ref:
        return str(ref)
    for key in ("reference_genome", "pangenome_genome"):
        block = site.get(key)
        if isinstance(block, dict) and block.get("fasta"):
            return str(block["fasta"])
    return "/work/genomes/homo_sapiens/grch38/fasta/genome.fa"


def _reference_gtf(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    env = os.environ.get("METHYL_COMPARE_REFERENCE_GTF")
    if env:
        return env
    site = _load_site()
    annotation = site.get("annotation") if isinstance(site.get("annotation"), dict) else {}
    gtf = annotation.get("gtf") or annotation.get("referenceGtf") or site.get("referenceGtf")
    if gtf:
        return str(gtf)
    return "/work/genomes/annotation/gencode/v50/gencode.v50.annotation.gtf"


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


def _list_tasks(db: Any, instance_id: int) -> List[Dict[str, Any]]:
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
        if tasks:
            return [t for t in tasks if isinstance(t, dict)]
    return []


def _task_action_names(tasks: Sequence[Mapping[str, Any]]) -> List[str]:
    names: List[str] = []
    for t in tasks:
        for key in ("action_name", "actionName", "capability", "name"):
            val = t.get(key)
            if val:
                names.append(str(val))
                break
    return names


def _download_root_fastqs(
    *,
    sample_id: str,
    sample_root: Path,
    fastq_storage: Mapping[str, Any],
    fastq_prefix: str,
) -> List[str]:
    """Download once into the sample root (lab/QNAP shape)."""
    ensure_import_paths()
    from pydantic import TypeAdapter

    from methyl_domain.fastq_storage import FastqStorageDefaults, merge_fastq_source
    from methyl_worker.fastq_source import download_from_source

    sample_root.mkdir(parents=True, exist_ok=True)
    prefix = fastq_prefix or f"{sample_id}/"
    defaults = TypeAdapter(FastqStorageDefaults).validate_python(dict(fastq_storage))
    source = merge_fastq_source(defaults, prefix)
    return download_from_source(source, sample_root)


def _resolve_samples(args: argparse.Namespace) -> List[Dict[str, Any]]:
    if args.samples_json:
        payload = _load_json(_resolve_path(args.samples_json))
        samples = payload.get("samples") if "samples" in payload else payload
        if isinstance(samples, dict):
            samples = samples.get("samples")
        if not isinstance(samples, list) or not samples:
            raise SystemExit(f"--samples-json must contain a samples list: {args.samples_json}")
        return [dict(s) for s in samples]
    return [dict(s) for s in DEFAULT_SAMPLES]


def _resolve_modes(args: argparse.Namespace, all_modes: Sequence[str]) -> List[str]:
    """Arms to run this invocation (arms are independent; one may take days)."""
    requested = [m.strip() for m in (args.modes or "").split(",") if m.strip()]
    if not requested:
        return list(all_modes)
    unknown = [m for m in requested if m not in all_modes]
    if unknown:
        raise SystemExit(f"--modes: unknown mode(s) {unknown}; choose from {list(all_modes)}")
    return [m for m in all_modes if m in requested]


def _thresholds_from_args(args: argparse.Namespace):
    ensure_import_paths()
    from methyl_utils.test_data_registry import SamplePrepCanaryThresholds

    if args.thresholds_json:
        return SamplePrepCanaryThresholds.model_validate(
            _load_json(_resolve_path(args.thresholds_json))
        )
    # Operator must supply thresholds via JSON for science gates; empty = report-only deltas.
    return SamplePrepCanaryThresholds()


def run_compare(args: argparse.Namespace) -> int:
    ensure_import_paths()
    from methyl_utils.testing.sample_prep_mode_compare import (
        COMPARE_MODES,
        build_mode_compare_report,
        build_sample_compare_block,
        build_start_payload,
        discover_root_fastqs,
        link_root_fastqs_into_mode,
        mode_sample_dir,
        sample_root_dir,
        validate_compare_arm,
        write_mode_compare_markdown,
    )

    if os.environ.get("WORKER_STUB_EXTERNAL") in {"1", "true", "TRUE", "yes"}:
        if not args.dry_run and not args.report_only:
            raise SystemExit(
                "WORKER_STUB_EXTERNAL is set; refuse real compare. Unset it or pass --dry-run."
            )

    samples = _resolve_samples(args)
    samples_base = Path(args.samples_base).expanduser().resolve()
    report_root = Path(args.report_dir).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = report_root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    modes = _resolve_modes(args, COMPARE_MODES)
    thresholds = _thresholds_from_args(args)
    reference_fasta = _reference_fasta(args.reference_fasta)
    reference_gtf = _reference_gtf(args.reference_gtf)
    # Always compile the current fixture so methylGrapher branches exist even when
    # MSSQL SamplePrep v7 is still the stale linear-only graph.
    sample_prep_program = str(
        REPO_ROOT / "workflow_engine" / "domain" / "fixtures" / "sample_prep.program.json"
    )

    qnap_storage: Optional[Dict[str, Any]] = None
    if args.fastq_storage_json:
        qnap_storage = _load_json(_resolve_path(args.fastq_storage_json))

    versions_file = Path(
        args.versions_file
        or os.environ.get("GOLIATH_ENV_DIR", "/work/goliath/env") + "/workflow_versions.json"
    )
    if not versions_file.is_file():
        versions_file = Path(
            os.environ.get("GOLIATH_ENV_DIR", "/work/goliath/env")
            + "/workflow_versions_mc.json"
        )
    if not versions_file.is_file():
        versions_file = REPO_ROOT / ".smoke" / "workflow_versions.json"
    versions = _load_versions(versions_file)
    sample_prep_vid = _sample_prep_version_id(versions)

    meta: Dict[str, Any] = {
        "stamp": stamp,
        "samples_base": str(samples_base),
        "reference_fasta": reference_fasta,
        "reference_gtf": reference_gtf,
        "versions_file": str(versions_file),
        "workflow_version_id": sample_prep_vid,
        "program_path": sample_prep_program,
        "reuse_local_fastq": bool(args.reuse_local_fastq),
        "modes": list(modes),
        "archive": "disabled",
    }

    planned_payloads: List[Dict[str, Any]] = []
    sample_blocks: List[Dict[str, Any]] = []
    instance_ids: Dict[str, Dict[str, int]] = {}

    for sample in samples:
        sample_id = str(sample["sampleId"])
        sample_root = sample_root_dir(samples_base, sample_id)
        sample_root.mkdir(parents=True, exist_ok=True)
        project_path = sample.get("projectPath") or args.project_path
        if not project_path:
            raise SystemExit(f"projectPath required for sample {sample_id}")
        primary = str(sample.get("primaryAnalyte") or "buffy_coat")
        fastq_prefix = str(sample.get("fastqPrefix") or f"{sample_id}/")

        if not args.report_only and not args.dry_run:
            existing = discover_root_fastqs(sample_root, sample_id)
            if args.reuse_local_fastq:
                if not existing:
                    raise SystemExit(
                        f"--reuse-local-fastq set but no non-empty root FASTQs for {sample_id} "
                        f"under {sample_root}"
                    )
            elif not existing:
                if qnap_storage is None:
                    raise SystemExit(
                        f"Root FASTQs missing for {sample_id} and --fastq-storage-json not set. "
                        "Download once to the sample root or pass --reuse-local-fastq."
                    )
                print(
                    json.dumps(
                        {"download_root_fastqs": sample_id, "sample_root": str(sample_root)},
                        indent=2,
                    ),
                    flush=True,
                )
                _download_root_fastqs(
                    sample_id=sample_id,
                    sample_root=sample_root,
                    fastq_storage=qnap_storage,
                    fastq_prefix=fastq_prefix,
                )

        arm_reports = {}
        instance_ids[sample_id] = {}
        for mode in modes:
            mode_dir = mode_sample_dir(sample_root, mode)
            mode_dir.mkdir(parents=True, exist_ok=True)
            # Mode-local file storage so download_fastq skips onto hardlinked root FASTQs.
            mode_fastq_storage = {"type": "file", "basePath": str(mode_dir)}
            payload = build_start_payload(
                sample_id=sample_id,
                mode=mode,
                sample_dir=mode_dir,
                project_path=project_path,
                workflow_version_id=None,
                primary_analyte=primary,
                reference_fasta=reference_fasta,
                reference_gtf=reference_gtf,
                fastq_storage=mode_fastq_storage,
                fastq_prefix=".",
                library_protocol=sample.get("libraryProtocol"),
                pipeline_procedure=sample.get("pipelineProcedure"),
                action_config=sample.get("actionConfig"),
                program_path=sample_prep_program,
            )
            planned_payloads.append({"sampleId": sample_id, "mode": mode, "body": payload})

            if args.dry_run:
                continue
            if args.report_only:
                report = validate_compare_arm(
                    mode=mode,
                    sample_id=sample_id,
                    sample_dir=mode_dir,
                    thresholds=thresholds,
                )
                arm_reports[mode] = report
                continue

            link_root_fastqs_into_mode(
                sample_root, mode, sample_id=sample_id, method=args.link_method
            )
            print(
                json.dumps({"starting": sample_id, "mode": mode, "sampleDir": str(mode_dir)}, indent=2),
                flush=True,
            )
            proc = subprocess.run(
                [_python(), "-m", "admin.study_start", "sample-prep-start", "-"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT / "workflow_engine"),
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            str(REPO_ROOT / "workflow_engine"),
                            os.environ.get("PYTHONPATH", ""),
                        ]
                    ),
                },
            )
            if proc.returncode != 0:
                print(proc.stdout)
                print(proc.stderr, file=sys.stderr)
                raise SystemExit(f"sample-prep-start failed for {sample_id}/{mode}")
            started = json.loads(proc.stdout)
            instance_id = int(started["instance_id"])
            instance_ids[sample_id][mode] = instance_id

            from rest.connection import resolve_connection_config
            from rest.db import open_gateway_db

            db_cfg = resolve_connection_config()
            db = open_gateway_db(db_cfg)
            try:
                status = _poll_instance(
                    db, instance_id, poll=args.poll_seconds, timeout=args.timeout
                )
                tasks = _list_tasks(db, instance_id)
            finally:
                db.close()
            actions = _task_action_names(tasks)
            report = validate_compare_arm(
                mode=mode,
                sample_id=sample_id,
                sample_dir=mode_dir,
                thresholds=thresholds,
                observed_actions=actions or None,
                task_rows=tasks or None,
            )
            from methyl_utils.testing.sample_prep_canary import CheckResult

            report.checks.append(
                CheckResult(
                    "workflow_completed",
                    status == "COMPLETED",
                    f"status={status} instance_id={instance_id}",
                )
            )
            arm_reports[mode] = report

        if args.dry_run:
            continue
        if "linear" in arm_reports and "pangenome_wgbs" in arm_reports:
            sample_blocks.append(
                build_sample_compare_block(
                    sample_id=sample_id,
                    linear=arm_reports["linear"],
                    wgbs=arm_reports["pangenome_wgbs"],
                    thresholds=thresholds,
                )
            )

    payloads_path = out_dir / "start_payloads.json"
    payloads_path.write_text(json.dumps(planned_payloads, indent=2) + "\n", encoding="utf-8")

    if args.dry_run:
        dry = {
            "dry_run": True,
            "meta": meta,
            "n_payloads": len(planned_payloads),
            "payloads_path": str(payloads_path),
            "samples": [s.get("sampleId") for s in samples],
        }
        dry_path = out_dir / "dry_run.json"
        dry_path.write_text(json.dumps(dry, indent=2) + "\n", encoding="utf-8")
        latest = report_root / "latest"
        if latest.is_symlink() or latest.exists():
            if latest.is_symlink() or latest.is_file():
                latest.unlink()
            elif latest.is_dir():
                shutil.rmtree(latest)
        latest.symlink_to(out_dir, target_is_directory=True)
        print(json.dumps(dry, indent=2))
        return 0

    meta["instance_ids"] = instance_ids
    report = build_mode_compare_report(
        sample_blocks=sample_blocks,
        thresholds=thresholds,
        meta=meta,
    )
    json_path = out_dir / "comparison.json"
    md_path = out_dir / "comparison.md"
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    write_mode_compare_markdown(report, md_path)
    latest = report_root / "latest"
    if latest.is_symlink() or latest.exists():
        if latest.is_symlink() or latest.is_file():
            latest.unlink()
        elif latest.is_dir():
            shutil.rmtree(latest)
    latest.symlink_to(out_dir, target_is_directory=True)
    print(
        json.dumps(
            {
                "report": str(json_path),
                "markdown": str(md_path),
                "overall_pass": report["overall_pass"],
                "out_dir": str(out_dir),
            },
            indent=2,
        )
    )
    return 0 if report["overall_pass"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compare SamplePrep linear vs pangenome_wgbs (experiment-only mode trees)"
    )
    p.add_argument(
        "--samples-base",
        default="/work/samples",
        help="Samples root (default /work/samples)",
    )
    p.add_argument(
        "--report-dir",
        default="/work/samples/_comparisons",
        help="Comparison report root (default /work/samples/_comparisons)",
    )
    p.add_argument(
        "--samples-json",
        default=None,
        help="Optional JSON with samples[] overrides (else plasma+buffy defaults)",
    )
    p.add_argument("--project-path", default=None, help="Fallback projectPath for all samples")
    p.add_argument(
        "--modes",
        default=None,
        help=(
            "Comma-separated arms to run (default both: linear,pangenome_wgbs). Run arms "
            "separately when the CPU-only pangenome arm needs a much longer timeout; "
            "combine with --report-only afterwards for the paired report."
        ),
    )
    p.add_argument(
        "--fastq-storage-json",
        default=None,
        help="QNAP/lab fastqStorage JSON used only for one-time root download",
    )
    p.add_argument(
        "--thresholds-json",
        default=None,
        help="Operator-set SamplePrepCanaryThresholds JSON (no Python science defaults)",
    )
    p.add_argument("--versions-file", default=None)
    p.add_argument("--reference-fasta", default=None)
    p.add_argument("--reference-gtf", default=None)
    p.add_argument("--poll-seconds", type=int, default=30)
    p.add_argument("--timeout", type=int, default=28800, help="Per-arm timeout seconds")
    p.add_argument(
        "--link-method",
        choices=("hardlink", "symlink", "copy"),
        default="hardlink",
        help="How to place root FASTQs into each mode sampleDir",
    )
    p.add_argument(
        "--reuse-local-fastq",
        action="store_true",
        help="Require existing non-empty root FASTQs; skip QNAP download",
    )
    p.add_argument(
        "--report-only",
        action="store_true",
        help="Do not start workflows; validate existing mode trees and write report",
    )
    p.add_argument("--dry-run", action="store_true", help="Plan start payloads only")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return run_compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
