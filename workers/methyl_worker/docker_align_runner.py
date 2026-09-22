"""Generic Docker methylation aligner (epi-GBS / alternate tools).

Operators pin image + argv template in actionConfig.docker_align / resolvedConfig.
No Parabricks-specific logic — keeps SamplePrep extensible without new handlers.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

FASTQ_SUFFIXES: Sequence[str] = (".fastq.gz", ".fq.gz", ".fastq", ".fq")


def _find_pair(sample_dir: Path, prefer_demux: bool = True) -> tuple[Path, Path]:
    files = sorted(
        p for p in sample_dir.iterdir() if p.is_file() and p.name.endswith(FASTQ_SUFFIXES)
    )
    if prefer_demux:
        demux = [p for p in files if "_demux_" in p.name]
        if len(demux) >= 2:
            files = demux
    r1 = next((p for p in files if "_R1" in p.name or ".R1." in p.name or p.name.endswith("_1.fastq.gz")), None)
    r2 = next((p for p in files if "_R2" in p.name or ".R2." in p.name or p.name.endswith("_2.fastq.gz")), None)
    if r1 is None or r2 is None:
        if len(files) >= 2:
            return files[0], files[1]
        raise RuntimeError(f"Need paired FASTQs in {sample_dir}")
    return r1, r2


def _render_argv(template: Sequence[str], mapping: Mapping[str, str]) -> List[str]:
    out: List[str] = []
    for part in template:
        s = str(part)
        for key, val in mapping.items():
            s = s.replace("{" + key + "}", val)
        out.append(s)
    return out


def run_docker_align(
    *,
    sample_id: str,
    sample_dir: str | Path,
    reference_fasta: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """Run a config-described Docker aligner; emit BAM + optional QC metrics tar."""
    payload = dict(input_json or {})
    resolved = dict(payload.get("resolvedConfig") or {})
    sample_path = Path(sample_dir).resolve()
    ref = Path(reference_fasta).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")
    if not ref.is_file():
        raise RuntimeError(f"referenceFasta not found: {ref}")

    image = (
        resolved.get("image")
        or payload.get("dockerAlignImage")
        or os.environ.get("METHYL_DOCKER_ALIGN_IMAGE")
    )
    if not image:
        raise RuntimeError(
            "sample.docker_align requires resolvedConfig.image "
            "(or METHYL_DOCKER_ALIGN_IMAGE)."
        )

    argv_tmpl = resolved.get("argv") or resolved.get("command") or []
    if isinstance(argv_tmpl, str):
        argv_tmpl = shlex.split(argv_tmpl)
    if not argv_tmpl:
        raise RuntimeError(
            "sample.docker_align requires resolvedConfig.argv "
            "(list of strings with {r1},{r2},{bam},{reference},{sample_dir} placeholders)."
        )

    r1, r2 = _find_pair(sample_path)
    bam_path = sample_path / f"{sample_id}.bam"
    metrics_dir = sample_path / "qc-metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    metrics_json = sample_path / f"{sample_id}.alignment_metrics.json"
    qc_tar = sample_path / f"{sample_id}.qc-metrics.tar"

    mapping = {
        "sample_id": sample_id,
        "sample_dir": str(sample_path),
        "r1": str(r1),
        "r2": str(r2),
        "bam": str(bam_path),
        "reference": str(ref),
        "metrics_dir": str(metrics_dir),
    }
    inner_argv = _render_argv(list(argv_tmpl), mapping)

    docker_extra = resolved.get("extra_docker_args") or resolved.get("extraDockerArgs") or []
    if isinstance(docker_extra, str):
        docker_extra = shlex.split(docker_extra)
    gpu_flags = resolved.get("gpu_flags") or resolved.get("gpuFlags") or []
    if isinstance(gpu_flags, str):
        gpu_flags = shlex.split(gpu_flags)

    # Mount sample dir + reference parent
    mounts = [
        "-v",
        f"{sample_path}:{sample_path}",
        "-v",
        f"{ref.parent}:{ref.parent}",
    ]
    cmd = ["docker", "run", "--rm", *gpu_flags, *docker_extra, *mounts, str(image), *inner_argv]
    logger.info("docker_align: %s", " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, check=True)

    if not bam_path.is_file():
        # Allow operator to write BAM under a configured relative name
        alt = resolved.get("bam_name")
        if alt:
            candidate = sample_path / str(alt)
            if candidate.is_file():
                bam_path = candidate
        if not bam_path.is_file():
            raise RuntimeError(f"docker_align did not produce BAM at {bam_path}")

    # Ensure a qc-metrics tar exists for sample.methyl_qc (empty placeholder OK).
    if not qc_tar.is_file():
        with tarfile.open(qc_tar, "w") as tar:
            for p in metrics_dir.iterdir():
                if p.is_file():
                    tar.add(p, arcname=p.name)
        if not metrics_json.is_file():
            metrics_json.write_text(
                '{"tool":"docker_align","sample_id":"%s","note":"metrics from docker_align"}\n'
                % sample_id,
                encoding="utf-8",
            )
            with tarfile.open(qc_tar, "a") as tar:
                tar.add(metrics_json, arcname=metrics_json.name)

    from methyl_worker.work_share import share_work_tree

    share_work_tree(sample_path)

    return {
        "sampleId": sample_id,
        "bamPath": str(bam_path),
        "metricsJson": str(metrics_json) if metrics_json.is_file() else None,
        "qcMetricsTar": str(qc_tar) if qc_tar.is_file() else None,
    }
