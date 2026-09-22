"""NVIDIA Clara Parabricks vg Giraffe pangenome alignment + QC metrics via Docker."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from methyl_worker.parabricks_runner import (
    ParabricksConfig,
    ParabricksPaths,
    _append_log,
    _clear_alignment_outputs,
    _docker_bin,
    _package_qc_metrics,
    _pick_bool,
    _resolve_paths,
    alignment_outputs_complete,
    resolve_paired_fastqs,
    resolve_parabricks_config,
)
from methyl_worker.work_share import (
    align_docker_user,
    docker_umask_prefix,
    docker_umask_suffix,
    share_work_tree,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PangenomeGraphBundle:
    gbz: Path
    dist: Path
    minimizer: Path
    zipcodes: Path
    ref_paths: Path
    linear_ref_fasta: Path

    @property
    def mount_root(self) -> Path:
        """Common read-only mount directory covering graph index files only."""
        return Path(
            os.path.commonpath(
                [
                    str(self.gbz.resolve().parent),
                    str(self.dist.resolve().parent),
                    str(self.minimizer.resolve().parent),
                    str(self.zipcodes.resolve().parent),
                    str(self.ref_paths.resolve().parent),
                ]
            )
        )


def resolve_pangenome_graph_bundle(
    *,
    site_path: str | Path | None = None,
    site: Mapping[str, object] | None = None,
) -> PangenomeGraphBundle:
    from methyl_utils.action_config_resolver import load_site_manifest, resolve_pangenome_genome

    site_data = dict(site) if site is not None else load_site_manifest(site_path)
    raw = resolve_pangenome_genome(site_data)
    bundle = PangenomeGraphBundle(
        gbz=Path(raw["gbz"]).expanduser().resolve(),
        dist=Path(raw["dist"]).expanduser().resolve(),
        minimizer=Path(raw["min"]).expanduser().resolve(),
        zipcodes=Path(raw["zipcodes"]).expanduser().resolve(),
        ref_paths=Path(raw["ref_paths"]).expanduser().resolve(),
        linear_ref_fasta=Path(raw["linear_ref_fasta"]).expanduser().resolve(),
    )
    for label, path in (
        ("gbz", bundle.gbz),
        ("dist", bundle.dist),
        ("min", bundle.minimizer),
        ("zipcodes", bundle.zipcodes),
        ("ref_paths", bundle.ref_paths),
        ("linear_ref_fasta", bundle.linear_ref_fasta),
    ):
        if not path.is_file():
            raise RuntimeError(f"pangenome_genome.{label} not found: {path}")
    return bundle


def _container_path(host_root: Path, host_path: Path) -> str:
    rel = host_path.resolve().relative_to(host_root.resolve())
    return f"/pangenome/{rel.as_posix()}"


def _build_giraffe_docker_command(
    cfg: ParabricksConfig,
    paths: ParabricksPaths,
    graph: PangenomeGraphBundle,
    fastqs: Sequence[Path],
) -> List[str]:
    mount_root = graph.mount_root
    in_fq_args: List[str] = []
    for fastq in fastqs:
        rel = fastq.relative_to(paths.sample_dir)
        in_fq_args.append(f"/workdir/{rel.as_posix()}")

    read_group = f"{paths.sample_id}_rg"
    return [
        _docker_bin(),
        "run",
        "--rm",
        *cfg.gpu_flags,
        "--user",
        align_docker_user(),
        "-v",
        f"{paths.sample_dir.resolve()}:/workdir",
        "-v",
        f"{paths.sample_dir.resolve()}:/outputdir",
        "-v",
        f"{mount_root.resolve()}:/pangenome:ro",
        "-w",
        "/workdir",
        *cfg.extra_docker_args,
        *docker_umask_prefix(),
        cfg.image,
        *docker_umask_suffix(
            "pbrun",
            "giraffe",
            f"--read-group={read_group}",
            f"--sample={paths.sample_id}",
            "--read-group-library=library",
            "--read-group-platform=ILLUMINA",
            f"--read-group-pu={paths.sample_id}",
            f"--gbz-name={_container_path(mount_root, graph.gbz)}",
            f"--dist-name={_container_path(mount_root, graph.dist)}",
            f"--minimizer-name={_container_path(mount_root, graph.minimizer)}",
            f"--zipcodes-name={_container_path(mount_root, graph.zipcodes)}",
            f"--ref-paths={_container_path(mount_root, graph.ref_paths)}",
            "--in-fq",
            in_fq_args[0],
            in_fq_args[1],
            f"--out-bam=/outputdir/{paths.bam_path.name}",
            f"--out-duplicate-metrics=/outputdir/{paths.dedup_metrics.name}",
            f"--logfile=/outputdir/{paths.log_path.name}",
        ),
    ]


def _build_collect_metrics_docker_command(
    cfg: ParabricksConfig,
    paths: ParabricksPaths,
    reference_fasta: Path,
) -> List[str]:
    genome_dir = reference_fasta.parent
    ref_basename = reference_fasta.name
    return [
        _docker_bin(),
        "run",
        "--rm",
        *cfg.gpu_flags,
        "--user",
        align_docker_user(),
        "-v",
        f"{paths.sample_dir.resolve()}:/workdir",
        "-v",
        f"{paths.sample_dir.resolve()}:/outputdir",
        "-v",
        f"{genome_dir.resolve()}:/genomes:ro",
        "-w",
        "/workdir",
        *cfg.extra_docker_args,
        *docker_umask_prefix(),
        cfg.image,
        *docker_umask_suffix(
            "pbrun",
            "collectmultiplemetrics",
            f"--ref=/genomes/{ref_basename}",
            f"--bam=/workdir/{paths.bam_path.name}",
            f"--out-qc-metrics-dir=/outputdir/{paths.qc_metrics_dir.name}",
            "--gen-all-metrics",
        ),
    ]


def run_giraffe_align(
    *,
    sample_id: str,
    sample_dir: str | Path,
    project: str | Path | None = None,
    input_json: Optional[Mapping[str, Any]] = None,
    parabricks_image: Optional[str] = None,
    site_path: str | Path | None = None,
) -> Dict[str, Optional[str]]:
    """
    Align paired FASTQs with Parabricks ``pbrun giraffe`` (HPRC graph, GRCh38 surjection),
    then regenerate WGBS QC metrics via ``pbrun collectmultiplemetrics``.
    """
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    cfg = resolve_parabricks_config(
        project_path=project or (input_json or {}).get("projectPath") or (input_json or {}).get("project"),
        input_json=input_json,
        parabricks_image=parabricks_image,
    )
    graph = resolve_pangenome_graph_bundle(site_path=site_path)
    paths = _resolve_paths(sample_path, sample_id, graph.linear_ref_fasta)

    force_realign = _pick_bool(input_json or {}, {}, "forceRealign", "forceRealign", default=False)
    if force_realign and alignment_outputs_complete(paths):
        logger.info("forceRealign: clearing existing pangenome alignment outputs for %s", sample_id)
        _clear_alignment_outputs(paths)

    if alignment_outputs_complete(paths):
        logger.info("Skipping Parabricks giraffe; outputs already present for %s", sample_id)
        _package_qc_metrics(paths)
        return _result_payload(paths)

    from methyl_worker.capabilities import assert_execute_gpu_prereqs

    assert_execute_gpu_prereqs("parabricks.giraffe", "sample.parabricks_giraffe")

    fastqs = resolve_paired_fastqs(sample_path, sample_id)
    paths.sample_dir.mkdir(parents=True, exist_ok=True)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)

    giraffe_cmd = _build_giraffe_docker_command(cfg, paths, graph, fastqs)
    logger.info("Running Parabricks giraffe for %s", sample_id)
    _append_log(paths.log_path, "COMMAND: " + " ".join(shlex.quote(part) for part in giraffe_cmd))
    _run_docker(giraffe_cmd, paths.log_path, step="giraffe")

    if not paths.bam_path.is_file():
        raise RuntimeError(f"Parabricks giraffe did not produce BAM: {paths.bam_path}")
    if not paths.dedup_metrics.is_file():
        raise RuntimeError(
            f"Parabricks giraffe did not produce duplicate metrics: {paths.dedup_metrics}"
        )

    metrics_cmd = _build_collect_metrics_docker_command(cfg, paths, graph.linear_ref_fasta)
    logger.info("Running Parabricks collectmultiplemetrics for %s", sample_id)
    _append_log(
        paths.log_path,
        "COMMAND: " + " ".join(shlex.quote(part) for part in metrics_cmd),
    )
    _run_docker(metrics_cmd, paths.log_path, step="collectmultiplemetrics")

    tar_path = _package_qc_metrics(paths)
    if tar_path is None and not paths.metrics_json.is_file():
        raise RuntimeError(
            f"Parabricks collectmultiplemetrics did not produce QC metrics for {sample_id}: "
            f"expected {paths.qc_metrics_dir} or {paths.qc_metrics_tar}"
        )

    if cfg.cleanup_tmp:
        from methyl_worker.parabricks_runner import _cleanup_tmp

        _cleanup_tmp(paths)

    return _result_payload(paths)


def _run_docker(cmd: List[str], log_path: Path, *, step: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.stdout:
        _append_log(log_path, f"[{step}] stdout:\n{proc.stdout}")
    if proc.stderr:
        _append_log(log_path, f"[{step}] stderr:\n{proc.stderr}")
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or f"Parabricks {step} failed"
        )


def _result_payload(paths: ParabricksPaths) -> Dict[str, Optional[str]]:
    share_work_tree(paths.sample_dir)
    metrics_json = str(paths.metrics_json) if paths.metrics_json.is_file() else None
    qc_tar = str(paths.qc_metrics_tar) if paths.qc_metrics_tar.is_file() else None
    return {
        "sampleId": paths.sample_id,
        "bamPath": str(paths.bam_path),
        "metricsJson": metrics_json,
        "qcMetricsTar": qc_tar,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Parabricks giraffe + collectmultiplemetrics via Docker")
    parser.add_argument("sample_id", help="Sample identifier")
    parser.add_argument("--sample-dir", help="Sample working directory")
    parser.add_argument("--image", help="Override METHYL_PARABRICKS_IMAGE")
    args = parser.parse_args(list(argv) if argv is not None else None)

    sample_dir = Path(
        args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}"
    )
    result = run_giraffe_align(
        sample_id=args.sample_id,
        sample_dir=sample_dir,
        parabricks_image=args.image,
    )
    for key, value in result.items():
        if value is not None:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
