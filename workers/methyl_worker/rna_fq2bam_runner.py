"""NVIDIA Clara Parabricks ``pbrun rna_fq2bam`` (STAR) RNA-Seq alignment + gene counts via Docker.

Parallel to :mod:`methyl_worker.parabricks_runner` (WGBS ``fq2bam_meth``). Produces a
coordinate-sorted BAM plus STAR ``ReadsPerGene.out.tab`` gene counts, normalized to a
per-sample ``{sample_id}.gene_counts.tsv`` for the RNA expression contract.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from methyl_worker.parabricks_runner import (
    _append_log,
    _docker_bin,
    _pick_bool,
    resolve_paired_fastqs,
    resolve_parabricks_config,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RnaFq2bamPaths:
    sample_dir: Path
    sample_id: str
    star_index_dir: Path
    gtf: Optional[Path]
    bam_path: Path
    gene_counts_path: Path
    star_output_dir: Path
    metrics_json: Path
    log_path: Path
    tmp_dir: Path


def _resolve_paths(
    sample_dir: Path, sample_id: str, star_index_dir: Path, gtf: Optional[Path]
) -> RnaFq2bamPaths:
    return RnaFq2bamPaths(
        sample_dir=sample_dir,
        sample_id=sample_id,
        star_index_dir=star_index_dir.resolve(),
        gtf=gtf.resolve() if gtf else None,
        bam_path=sample_dir / f"{sample_id}.rna.bam",
        gene_counts_path=sample_dir / f"{sample_id}.gene_counts.tsv",
        star_output_dir=sample_dir / f"{sample_id}.star",
        metrics_json=sample_dir / f"{sample_id}.rna.json",
        log_path=sample_dir / f"{sample_id}.rna_fq2bam.log",
        tmp_dir=sample_dir / "tmp_rna",
    )


def _resolve_rna_reference(
    input_json: Mapping[str, Any],
    site_path: str | Path | None,
) -> Dict[str, str]:
    from methyl_utils.action_config_resolver import resolve_from_task_input, resolve_rna_reference

    align_cfg = resolve_from_task_input("rna_align", dict(input_json))
    ref: Dict[str, str] = {}
    site = input_json.get("siteConfig")
    if isinstance(site, dict):
        ref = resolve_rna_reference(site)
    elif site_path:
        from methyl_utils.action_config_resolver import load_site_manifest

        ref = resolve_rna_reference(load_site_manifest(site_path))
    else:
        ref = resolve_rna_reference()
    # resolvedConfig / actionConfig may override site pins.
    for key in ("star_index_dir", "gtf", "reference_fasta"):
        if align_cfg.get(key):
            ref[key] = str(align_cfg[key])
    return ref


def outputs_complete(paths: RnaFq2bamPaths) -> bool:
    return paths.bam_path.is_file() and paths.gene_counts_path.is_file()


def _build_docker_command(cfg, paths: RnaFq2bamPaths, fastqs: Sequence[Path]) -> List[str]:
    uid = os.getuid()
    gid = os.getgid()
    in_fq_args: List[str] = []
    for fastq in fastqs:
        rel = fastq.relative_to(paths.sample_dir)
        in_fq_args.append(f"/workdir/{rel.as_posix()}")

    index_mount = paths.star_index_dir
    cmd: List[str] = [
        _docker_bin(),
        "run",
        "--rm",
        *cfg.gpu_flags,
        "--user",
        f"{uid}:{gid}",
        "-v",
        f"{paths.sample_dir.resolve()}:/workdir",
        "-v",
        f"{paths.sample_dir.resolve()}:/outputdir",
        "-v",
        f"{index_mount.resolve()}:/rna_index:ro",
    ]
    gtf_arg: List[str] = []
    if paths.gtf is not None:
        cmd += ["-v", f"{paths.gtf.parent.resolve()}:/annotation:ro"]
        gtf_arg = [f"--output-dir=/outputdir/{paths.star_output_dir.name}"]
    cmd += [
        "-w",
        "/workdir",
        *cfg.extra_docker_args,
        cfg.image,
        "pbrun",
        "rna_fq2bam",
        "--genome-lib-dir=/rna_index",
        "--in-fq",
        in_fq_args[0],
        in_fq_args[1],
        f"--out-bam=/outputdir/{paths.bam_path.name}",
        f"--output-dir=/outputdir/{paths.star_output_dir.name}",
        f"--read-files-command=zcat",
        f"--logfile=/outputdir/{paths.log_path.name}",
        f"--tmp-dir=/outputdir/{paths.tmp_dir.name}",
        f"--num-threads={cfg.bwa_threads}",
    ]
    return cmd


def _normalize_gene_counts(paths: RnaFq2bamPaths) -> None:
    """Copy STAR ReadsPerGene.out.tab into a canonical per-sample gene_counts.tsv.

    STAR emits four columns: gene_id, unstranded, forward, reverse. We keep gene_id
    and the unstranded count (column 2) as the portable per-sample count vector; the
    RNA expression registration step decides library-strandedness normalization.
    """
    star_counts = paths.star_output_dir / "ReadsPerGene.out.tab"
    if not star_counts.is_file():
        # Some Parabricks builds write the STAR prefix at the output root.
        for candidate in paths.sample_dir.glob(f"{paths.sample_id}*ReadsPerGene.out.tab"):
            star_counts = candidate
            break
    if not star_counts.is_file():
        raise RuntimeError(
            f"rna_fq2bam did not produce STAR gene counts (ReadsPerGene.out.tab) for {paths.sample_id}"
        )
    lines_out: List[str] = ["gene_id\tcount\n"]
    with open(star_counts, encoding="utf-8") as handle:
        for row in handle:
            parts = row.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            gene_id = parts[0]
            if gene_id.startswith("N_"):  # STAR summary rows: N_unmapped, N_multimapping, ...
                continue
            lines_out.append(f"{gene_id}\t{parts[1]}\n")
    paths.gene_counts_path.write_text("".join(lines_out), encoding="utf-8")


def run_rna_fq2bam(
    *,
    sample_id: str,
    sample_dir: str | Path,
    project: str | Path | None = None,
    input_json: Optional[Mapping[str, Any]] = None,
    parabricks_image: Optional[str] = None,
    site_path: str | Path | None = None,
) -> Dict[str, Optional[str]]:
    """Align RNA-Seq FASTQs and quantify gene counts with Parabricks ``pbrun rna_fq2bam``."""
    payload = dict(input_json or {})
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    ref = _resolve_rna_reference(payload, site_path)
    star_index = ref.get("star_index_dir")
    if not star_index:
        raise RuntimeError(
            "rna_fq2bam requires site rna_reference.star_index_dir (STAR genome index)"
        )
    star_index_dir = Path(star_index).expanduser().resolve()
    if not star_index_dir.is_dir():
        raise RuntimeError(f"rna_reference.star_index_dir not found: {star_index_dir}")
    gtf = Path(ref["gtf"]).expanduser().resolve() if ref.get("gtf") else None

    cfg = resolve_parabricks_config(
        project_path=project or payload.get("projectPath") or payload.get("project"),
        input_json=payload,
        parabricks_image=parabricks_image,
    )
    paths = _resolve_paths(sample_path, sample_id, star_index_dir, gtf)

    force = _pick_bool(payload, {}, "forceRealign", "forceRealign", default=False)
    if force and outputs_complete(paths):
        for p in (paths.bam_path, paths.gene_counts_path, paths.metrics_json):
            if p.is_file():
                p.unlink()
        if paths.star_output_dir.is_dir():
            shutil.rmtree(paths.star_output_dir, ignore_errors=True)

    if outputs_complete(paths):
        logger.info("Skipping rna_fq2bam; outputs already present for %s", sample_id)
        return _result_payload(paths)

    fastqs = resolve_paired_fastqs(sample_path, sample_id)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)
    docker_cmd = _build_docker_command(cfg, paths, fastqs)
    logger.info("Running Parabricks rna_fq2bam for %s", sample_id)
    _append_log(paths.log_path, "COMMAND: " + " ".join(shlex.quote(part) for part in docker_cmd))

    proc = subprocess.run(docker_cmd, capture_output=True, text=True, check=False)
    if proc.stdout:
        _append_log(paths.log_path, proc.stdout)
    if proc.stderr:
        _append_log(paths.log_path, proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Parabricks rna_fq2bam failed")

    if not paths.bam_path.is_file():
        raise RuntimeError(f"rna_fq2bam did not produce BAM: {paths.bam_path}")
    _normalize_gene_counts(paths)

    if cfg.cleanup_tmp and paths.tmp_dir.is_dir():
        shutil.rmtree(paths.tmp_dir, ignore_errors=True)

    return _result_payload(paths)


def _result_payload(paths: RnaFq2bamPaths) -> Dict[str, Optional[str]]:
    return {
        "sampleId": paths.sample_id,
        "bamPath": str(paths.bam_path) if paths.bam_path.is_file() else None,
        "geneCountsPath": str(paths.gene_counts_path) if paths.gene_counts_path.is_file() else None,
        "abundancePath": None,
        "metricsJson": str(paths.metrics_json) if paths.metrics_json.is_file() else None,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Parabricks rna_fq2bam via Docker")
    parser.add_argument("sample_id", help="Sample identifier")
    parser.add_argument("--sample-dir", help="Sample working directory")
    parser.add_argument("--image", help="Override METHYL_PARABRICKS_IMAGE")
    parser.add_argument("--site-config", help="Path to site manifest (rna_reference)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    sample_dir = Path(
        args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}"
    )
    result = run_rna_fq2bam(
        sample_id=args.sample_id,
        sample_dir=sample_dir,
        parabricks_image=args.image,
        site_path=args.site_config,
    )
    for key, value in result.items():
        if value is not None:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
