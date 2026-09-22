"""NVIDIA Clara Parabricks ``pbrun kallisto`` pseudo-alignment quantification via Docker.

Parallel to :mod:`methyl_worker.rna_fq2bam_runner`. Produces transcript-level
``abundance.tsv`` (est_counts + TPM); the RNA expression registration step aggregates
transcripts to genes using the site ``rna_reference.tx2gene`` map.
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
class KallistoPaths:
    sample_dir: Path
    sample_id: str
    index_path: Path
    output_dir: Path
    abundance_path: Path
    run_info_path: Path
    log_path: Path


def _resolve_paths(sample_dir: Path, sample_id: str, index_path: Path) -> KallistoPaths:
    out_dir = sample_dir / f"{sample_id}.kallisto"
    return KallistoPaths(
        sample_dir=sample_dir,
        sample_id=sample_id,
        index_path=index_path.resolve(),
        output_dir=out_dir,
        abundance_path=out_dir / "abundance.tsv",
        run_info_path=out_dir / "run_info.json",
        log_path=sample_dir / f"{sample_id}.kallisto.log",
    )


def _resolve_index(input_json: Mapping[str, Any], site_path: str | Path | None) -> str:
    from methyl_utils.action_config_resolver import (
        load_site_manifest,
        resolve_from_task_input,
        resolve_rna_reference,
    )

    align_cfg = resolve_from_task_input("rna_align", dict(input_json))
    if align_cfg.get("kallisto_index"):
        return str(align_cfg["kallisto_index"])
    site = input_json.get("siteConfig")
    if isinstance(site, dict):
        ref = resolve_rna_reference(site)
    elif site_path:
        ref = resolve_rna_reference(load_site_manifest(site_path))
    else:
        ref = resolve_rna_reference()
    return str(ref.get("kallisto_index") or "")


def outputs_complete(paths: KallistoPaths) -> bool:
    return paths.abundance_path.is_file()


def _build_docker_command(cfg, paths: KallistoPaths, fastqs: Sequence[Path]) -> List[str]:
    uid = os.getuid()
    gid = os.getgid()
    in_fq_args: List[str] = []
    for fastq in fastqs:
        rel = fastq.relative_to(paths.sample_dir)
        in_fq_args.append(f"/workdir/{rel.as_posix()}")

    index_dir = paths.index_path.parent
    return [
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
        f"{index_dir.resolve()}:/rna_index:ro",
        "-w",
        "/workdir",
        *cfg.extra_docker_args,
        cfg.image,
        "pbrun",
        "kallisto",
        f"--index=/rna_index/{paths.index_path.name}",
        f"--output-dir=/outputdir/{paths.output_dir.name}",
        "--in-fq",
        in_fq_args[0],
        in_fq_args[1],
        f"--num-threads={cfg.bwa_threads}",
    ]


def run_kallisto(
    *,
    sample_id: str,
    sample_dir: str | Path,
    project: str | Path | None = None,
    input_json: Optional[Mapping[str, Any]] = None,
    parabricks_image: Optional[str] = None,
    site_path: str | Path | None = None,
) -> Dict[str, Optional[str]]:
    """Quantify RNA-Seq FASTQs with Parabricks ``pbrun kallisto`` (pseudo-alignment)."""
    payload = dict(input_json or {})
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    index_raw = _resolve_index(payload, site_path)
    if not index_raw:
        raise RuntimeError("kallisto requires site rna_reference.kallisto_index")
    index_path = Path(index_raw).expanduser().resolve()
    if not index_path.is_file():
        raise RuntimeError(f"rna_reference.kallisto_index not found: {index_path}")

    cfg = resolve_parabricks_config(
        project_path=project or payload.get("projectPath") or payload.get("project"),
        input_json=payload,
        parabricks_image=parabricks_image,
    )
    paths = _resolve_paths(sample_path, sample_id, index_path)

    force = _pick_bool(payload, {}, "forceRealign", "forceRealign", default=False)
    if force and paths.output_dir.is_dir():
        shutil.rmtree(paths.output_dir, ignore_errors=True)

    if outputs_complete(paths):
        logger.info("Skipping kallisto; outputs already present for %s", sample_id)
        return _result_payload(paths)

    fastqs = resolve_paired_fastqs(sample_path, sample_id)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    docker_cmd = _build_docker_command(cfg, paths, fastqs)
    logger.info("Running Parabricks kallisto for %s", sample_id)
    _append_log(paths.log_path, "COMMAND: " + " ".join(shlex.quote(part) for part in docker_cmd))

    proc = subprocess.run(docker_cmd, capture_output=True, text=True, check=False)
    if proc.stdout:
        _append_log(paths.log_path, proc.stdout)
    if proc.stderr:
        _append_log(paths.log_path, proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Parabricks kallisto failed")

    if not paths.abundance_path.is_file():
        raise RuntimeError(f"kallisto did not produce abundance.tsv: {paths.abundance_path}")

    return _result_payload(paths)


def _result_payload(paths: KallistoPaths) -> Dict[str, Optional[str]]:
    return {
        "sampleId": paths.sample_id,
        "bamPath": None,
        "geneCountsPath": None,
        "abundancePath": str(paths.abundance_path) if paths.abundance_path.is_file() else None,
        "metricsJson": str(paths.run_info_path) if paths.run_info_path.is_file() else None,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Parabricks kallisto via Docker")
    parser.add_argument("sample_id", help="Sample identifier")
    parser.add_argument("--sample-dir", help="Sample working directory")
    parser.add_argument("--image", help="Override METHYL_PARABRICKS_IMAGE")
    parser.add_argument("--site-config", help="Path to site manifest (rna_reference)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    sample_dir = Path(
        args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}"
    )
    result = run_kallisto(
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
