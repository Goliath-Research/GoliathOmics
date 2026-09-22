#!/usr/bin/env python3
"""
Batch-run sample QC backfill (V2 JSON + full guardrails) for cohort CSV sample lists.

Default cohort: 15 healthy (healthy_b.csv) + 15 PCa (pca_b.csv) buffy-coat samples
under /work/samples, matching project_Buffy_healthy_vs_PCa.

Examples:
  source .venv/bin/activate

  # Dry run — list resolved sample folders
  python scripts/batch_sample_qc_backfill.py --dry-run

  # Full backfill with buffy_coat analyte profile (alignment + bisulfite + flagstat)
  python scripts/batch_sample_qc_backfill.py \\
    --output-dir /work/projects/prostate-cancer/alignment_qc_backfill

  # Use pipeline project alignment_qc step_config
  python scripts/batch_sample_qc_backfill.py \\
    --project /work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json \\
    --output-dir /work/AlignmentQC
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from methyl_alignment_qc.core.bam_flagstat import flagstat_bam_preflight_error
from methyl_alignment_qc.cli.sample_qc_backfill import (
    _failed_guardrail_keys,
    format_bam_flagstat_status,
    print_guardrail_summary,
    resolve_backfill_kwargs,
)
from methyl_alignment_qc.core.writer import build_sample_qc_v2_dict, write_sample_qc_json

DEFAULT_HEALTHY_CSV = Path("/work/projects/prostate-cancer/data/healthy_b.csv")
DEFAULT_PCA_CSV = Path("/work/projects/prostate-cancer/data/pca_b.csv")
DEFAULT_SAMPLES_BASE = Path("/work/samples")
DEFAULT_OUTPUT_DIR = Path("/work/projects/prostate-cancer/alignment_qc_backfill")
DEFAULT_PROJECT = Path("/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json")


@dataclass
class SampleJob:
    sample_id: str
    group: str
    sample_dir: Path
    output_path: Path


@dataclass
class SampleResult:
    sample_id: str
    group: str
    sample_dir: str
    output_path: str
    status: str
    overall_pass: Optional[bool] = None
    failed_guardrails: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_sample_ids(csv_path: Path) -> List[str]:
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Sample list CSV not found: {csv_path}")

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            col = "sample_id" if "sample_id" in reader.fieldnames else reader.fieldnames[0]
            ids = [str(row[col]).strip() for row in reader if str(row.get(col, "")).strip()]
        else:
            f.seek(0)
            plain = csv.reader(f)
            ids = [row[0].strip() for row in plain if row and row[0].strip() and row[0].strip() != "sample"]

    seen: set[str] = set()
    ordered: List[str] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def build_jobs(
    *,
    healthy_csv: Path,
    pca_csv: Path,
    samples_base: Path,
    output_dir: Path,
) -> List[SampleJob]:
    jobs: List[SampleJob] = []
    for group, csv_path in (("healthy", healthy_csv), ("PCa", pca_csv)):
        for sample_id in load_sample_ids(csv_path):
            sample_dir = samples_base / sample_id
            output_path = output_dir / f"{sample_id}.json"
            jobs.append(
                SampleJob(
                    sample_id=sample_id,
                    group=group,
                    sample_dir=sample_dir,
                    output_path=output_path,
                )
            )
    return jobs


def _preflight_sample_dir(sample_dir: Path, sample_id: str) -> Optional[str]:
    if not sample_dir.is_dir():
        return f"sample directory not found: {sample_dir}"
    dedup = sample_dir / f"{sample_id}.deduplicate_metrics.txt"
    metrics_json = sample_dir / f"{sample_id}.json"
    metrics_tar = sample_dir / f"{sample_id}.qc-metrics.tar"
    if not dedup.is_file():
        return f"missing Picard dedup metrics: {dedup}"
    if metrics_tar.is_file():
        return None
    if metrics_json.is_file():
        try:
            payload = json.loads(metrics_json.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "quality_yield" in payload:
                return None
        except (OSError, json.JSONDecodeError):
            pass
    tars = list(sample_dir.glob("*.qc-metrics.tar"))
    if len(tars) == 1:
        return None
    return (
        f"missing Parabricks metrics: expected {metrics_tar} or full {metrics_json} "
        f"(found {len(tars)} *.qc-metrics.tar)"
    )


def run_job(
    job: SampleJob,
    *,
    kwargs: Dict[str, Any],
    dry_run: bool,
    verbose: bool = False,
    write_sample_dir: bool = False,
) -> SampleResult:
    base = SampleResult(
        sample_id=job.sample_id,
        group=job.group,
        sample_dir=str(job.sample_dir),
        output_path=str(job.output_path),
        status="pending",
    )
    preflight_err = _preflight_sample_dir(job.sample_dir, job.sample_id)
    if preflight_err:
        base.status = "skipped"
        base.error = preflight_err
        return base

    if dry_run:
        bam = job.sample_dir / f"{job.sample_id}.bam"
        base.status = "dry_run"
        preflight = flagstat_bam_preflight_error(bam)
        if preflight:
            base.error = f"note: {preflight}; flagstat guardrails will fail"
        else:
            size_gb = bam.stat().st_size / (1024**3)
            base.error = f"note: BAM present ({size_gb:.1f} GiB)"
        return base

    bam = job.sample_dir / f"{job.sample_id}.bam"
    if verbose and bam.is_file() and bam.stat().st_size > 0:
        cache = job.sample_dir / f"{job.sample_id}.flagstat.txt"
        if cache.is_file() and cache.stat().st_mtime >= bam.stat().st_mtime:
            print(f"  flagstat: using cache {cache}", flush=True)
        else:
            print(
                f"  flagstat: running samtools on BAM ({bam.stat().st_size / (1024**3):.1f} GiB); "
                "this can take many minutes per sample",
                flush=True,
            )

    try:
        payload = build_sample_qc_v2_dict(
            job.sample_dir,
            output_path_for_history=job.output_path,
            **kwargs,
        )
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        write_sample_qc_json(payload, job.output_path)
        if write_sample_dir:
            sample_qc_path = job.sample_dir / f"{job.sample_id}.sample_qc.json"
            write_sample_qc_json(payload, sample_qc_path)
        guardrails = payload.get("guardrails") or {}
        base.status = "ok"
        base.overall_pass = guardrails.get("overall_pass")
        base.failed_guardrails = _failed_guardrail_keys(guardrails)
        if not verbose:
            bam_line = format_bam_flagstat_status(payload, sample_dir=job.sample_dir)
            if bam_line:
                print(bam_line, flush=True)
        return base
    except Exception as exc:
        base.status = "error"
        base.error = str(exc)
        return base


def write_manifest(results: Sequence[SampleResult], manifest_path: Path, *, meta: Dict[str, Any]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": _utc_now_iso(),
        **meta,
        "samples": [r.__dict__ for r in results],
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_summary_tsv(results: Sequence[SampleResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sample_id",
                "group",
                "status",
                "overall_pass",
                "failed_guardrails",
                "output_path",
                "error",
            ],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "sample_id": r.sample_id,
                    "group": r.group,
                    "status": r.status,
                    "overall_pass": r.overall_pass,
                    "failed_guardrails": ";".join(r.failed_guardrails),
                    "output_path": r.output_path,
                    "error": r.error or "",
                }
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batch sample QC backfill for healthy + PCa cohort CSVs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--healthy-csv",
        type=Path,
        default=DEFAULT_HEALTHY_CSV,
        help=f"Healthy sample list CSV (default: {DEFAULT_HEALTHY_CSV})",
    )
    parser.add_argument(
        "--pca-csv",
        type=Path,
        default=DEFAULT_PCA_CSV,
        help=f"PCa sample list CSV (default: {DEFAULT_PCA_CSV})",
    )
    parser.add_argument(
        "--samples-base",
        type=Path,
        default=DEFAULT_SAMPLES_BASE,
        help=f"Base directory for sample folders (default: {DEFAULT_SAMPLES_BASE})",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for per-sample QC JSON files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--project",
        "-p",
        type=Path,
        default=None,
        help=f"Optional project JSON for alignment_qc config (e.g. {DEFAULT_PROJECT})",
    )
    parser.add_argument(
        "--step-override",
        type=Path,
        default=None,
        help="Optional alignment_qc step override JSON (with --project)",
    )
    parser.add_argument(
        "--analyte",
        choices=["cfdna", "buffy_coat", "combined"],
        default="buffy_coat",
        help="Analyte profile when --project is not set (default: buffy_coat)",
    )
    parser.add_argument("--no-validate", action="store_true", help="Skip schema validation")
    parser.add_argument("--dry-run", action="store_true", help="List samples and preflight only")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first processing error (default: continue)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Manifest JSON path (default: {output_dir}/backfill_manifest.json)",
    )
    parser.add_argument(
        "--summary-tsv",
        type=Path,
        default=None,
        help="Summary TSV path (default: {output_dir}/backfill_summary.tsv)",
    )
    parser.add_argument(
        "--write-sample-dir",
        action="store_true",
        help="Also write full V2 JSON to {sample_dir}/{sample_id}.sample_qc.json (not the Parabricks stub {sample_id}.json)",
    )
    parser.add_argument(
        "--force-flagstat",
        action="store_true",
        help="Re-run samtools flagstat even when {sample_id}.flagstat.txt cache is fresh",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print per-sample guardrail summary")
    args = parser.parse_args(argv)

    try:
        jobs = build_jobs(
            healthy_csv=args.healthy_csv,
            pca_csv=args.pca_csv,
            samples_base=args.samples_base,
            output_dir=args.output_dir,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not jobs:
        print("No samples found in cohort CSVs.", file=sys.stderr)
        return 1

    kwargs = resolve_backfill_kwargs(
        project=args.project,
        step_override=args.step_override,
        step_config=None,
        analyte=None if args.project is not None else args.analyte,
        validate_schema=not args.no_validate,
    )
    kwargs["force_flagstat"] = args.force_flagstat

    print(f"Samples: {len(jobs)} ({sum(1 for j in jobs if j.group == 'healthy')} healthy, "
          f"{sum(1 for j in jobs if j.group == 'PCa')} PCa)")
    print(f"Output dir: {args.output_dir}")
    print(
        "Note: V2 QC JSON is written under --output-dir. "
        "/work/samples/{id}/{id}.json is often a Parabricks guardrails stub only — "
        "use --write-sample-dir for {id}.sample_qc.json in each sample folder.",
    )
    if args.project:
        print(f"Project config: {args.project}")
    elif args.analyte:
        print(f"Analyte profile: {args.analyte}")
    if args.dry_run:
        print("Dry run — no JSON will be written.")

    results: List[SampleResult] = []
    for i, job in enumerate(jobs, start=1):
        print(f"[{i}/{len(jobs)}] {job.group}\t{job.sample_id}", flush=True)
        result = run_job(
            job,
            kwargs=kwargs,
            dry_run=args.dry_run,
            verbose=args.verbose,
            write_sample_dir=args.write_sample_dir,
        )
        results.append(result)

        if result.status == "ok" and args.verbose:
            payload = json.loads(Path(result.output_path).read_text(encoding="utf-8"))
            print_guardrail_summary(payload, sample_dir=job.sample_dir)
        elif result.status in {"skipped", "error"}:
            print(f"  {result.status}: {result.error}", file=sys.stderr)
            if result.status == "error" and args.fail_fast:
                break
        elif result.status == "dry_run" and result.error:
            print(f"  {result.error}", file=sys.stderr)

    manifest_path = args.manifest or (args.output_dir / "backfill_manifest.json")
    summary_path = args.summary_tsv or (args.output_dir / "backfill_summary.tsv")
    if not args.dry_run:
        write_manifest(
            results,
            manifest_path,
            meta={
                "healthy_csv": str(args.healthy_csv),
                "pca_csv": str(args.pca_csv),
                "samples_base": str(args.samples_base),
                "output_dir": str(args.output_dir),
                "project": str(args.project) if args.project else None,
                "analyte": None if args.project else args.analyte,
            },
        )
        write_summary_tsv(results, summary_path)
        print(f"Manifest: {manifest_path}")
        print(f"Summary:  {summary_path}")

    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    errors = sum(1 for r in results if r.status == "error")
    dry = sum(1 for r in results if r.status == "dry_run")
    print(f"Done: ok={ok} skipped={skipped} errors={errors} dry_run={dry}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
