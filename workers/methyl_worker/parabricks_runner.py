"""Linear WGBS Align: Clara Parabricks fq2bam_meth or portable MojoFq2bamMeth."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from methyl_worker.fastq_pairs import (  # noqa: F401 — re-export for aligner callers/tests
    FASTQ_SUFFIXES,
    canonical_trimmed_fastqs,
    resolve_paired_fastqs,
)
from methyl_worker.work_share import (
    align_docker_user,
    docker_umask_prefix,
    docker_umask_suffix,
    share_work_tree,
)

try:
    from methyl_worker.work_share import append_work_text
except ImportError:  # stale sister: work_share loaded before append_work_text existed
    from methyl_worker.work_share_compat import stale_append_work_text as append_work_text

logger = logging.getLogger(__name__)

DEFAULT_MOJO_IMAGE = "goliath/methylgrapher:1.70-mojo"


@dataclass(frozen=True)
class ParabricksConfig:
    image: str
    gpu_flags: tuple[str, ...]
    bwa_threads: int
    extra_docker_args: tuple[str, ...]
    cleanup_tmp: bool
    engine: str = "parabricks"  # parabricks | mojo
    align_device: str = "auto"
    write_methylation_tags: bool = False


@dataclass(frozen=True)
class ParabricksPaths:
    sample_dir: Path
    sample_id: str
    reference_fasta: Path
    bam_path: Path
    qc_metrics_dir: Path
    qc_metrics_tar: Path
    metrics_json: Path
    dedup_metrics: Path
    log_path: Path
    tmp_dir: Path


def _pick(
    input_json: Mapping[str, Any],
    step_cfg: Mapping[str, Any],
    input_key: str,
    step_key: str,
) -> Any:
    if input_key in input_json and input_json[input_key] is not None:
        return input_json[input_key]
    return step_cfg.get(step_key)


def _pick_int(
    input_json: Mapping[str, Any],
    step_cfg: Mapping[str, Any],
    input_key: str,
    step_key: str,
    default: Optional[int] = None,
) -> Optional[int]:
    value = _pick(input_json, step_cfg, input_key, step_key)
    if value is None:
        return default
    return int(value)


def _pick_bool(
    input_json: Mapping[str, Any],
    step_cfg: Mapping[str, Any],
    input_key: str,
    step_key: str,
    default: bool,
) -> bool:
    value = _pick(input_json, step_cfg, input_key, step_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no"}
    return bool(value)


def resolve_collectmultiplemetrics_config(
    *,
    project_path: str | Path | None = None,
    input_json: Optional[Mapping[str, Any]] = None,
) -> ParabricksConfig:
    """Resolve Docker settings for ``pbrun collectmultiplemetrics``.

    Always uses the Clara Parabricks image. Callers often pass a *different*
    action's ``resolvedConfig`` (e.g. methylgrapher_wgbs with ``engine=mojo`` /
    ``image=…methylgrapher:*-mojo``). ``resolve_from_task_input`` returns that
    slice as-is for any ``action_key``, which would put ``pbrun`` in an image
    that does not contain it. Strip ``resolvedConfig`` here and pin
    ``METHYL_PARABRICKS_IMAGE`` (or ``actionConfig.parabricks.parabricksImage``).
    """
    payload = dict(input_json or {})
    metrics_input: Dict[str, Any] = {}
    proj = project_path or payload.get("projectPath") or payload.get("project")
    if proj not in (None, ""):
        metrics_input["projectPath"] = proj
        metrics_input["project"] = proj

    ac = payload.get("actionConfig")
    pb_slice: Dict[str, Any] = {}
    if isinstance(ac, dict) and isinstance(ac.get("parabricks"), dict):
        pb_slice = dict(ac["parabricks"])
        # Metrics need NVIDIA Clara ``pbrun`` even when linear align engine is mojo.
        pb_slice["engine"] = "parabricks"
        # Drop align-image keys that may point at the mojo-align image.
        for key in ("image", "mojoImage", "mojo_image"):
            pb_slice.pop(key, None)
        metrics_input["actionConfig"] = {"parabricks": pb_slice}

    clara = os.environ.get("METHYL_PARABRICKS_IMAGE", "").strip()
    if not clara:
        candidate = str(pb_slice.get("parabricksImage") or "").strip()
        if candidate and "methylgrapher" not in candidate:
            clara = candidate
    if not clara:
        raise RuntimeError(
            "collectmultiplemetrics requires METHYL_PARABRICKS_IMAGE "
            "(Clara Parabricks image with pbrun); Mojo methylgrapher images do not ship pbrun"
        )

    cfg = resolve_parabricks_config(
        project_path=proj,
        input_json=metrics_input,
        parabricks_image=clara,
    )
    if cfg.engine == "mojo" or "methylgrapher" in cfg.image:
        # Belt-and-suspenders: never invoke pbrun in the Mojo align image.
        return ParabricksConfig(
            image=clara,
            gpu_flags=cfg.gpu_flags
            or tuple(shlex.split(os.environ.get("METHYL_PARABRICKS_GPU_FLAGS", "--gpus all"))),
            bwa_threads=cfg.bwa_threads,
            extra_docker_args=cfg.extra_docker_args,
            cleanup_tmp=cfg.cleanup_tmp,
            engine="parabricks",
            align_device=cfg.align_device,
        )
    return cfg


def resolve_parabricks_config(
    *,
    project_path: str | Path | None = None,
    input_json: Optional[Mapping[str, Any]] = None,
    parabricks_image: Optional[str] = None,
    bwa_threads: Optional[int] = None,
) -> ParabricksConfig:
    """Resolve Parabricks settings from task input_json, resolved action config, then env."""
    payload = dict(input_json or {})
    step_cfg: Dict[str, Any] = {}
    regulatory: Dict[str, Any] = {}
    if project_path:
        from methyl_utils import load_project

        project_file = Path(str(project_path)).expanduser().resolve()
        if project_file.is_file():
            project = load_project(str(project_file))
            regulatory = dict(project.get_regulatory_config() or {})

    from methyl_utils.action_config_resolver import resolve_from_task_input, resolve_for_project

    if payload:
        step_cfg = dict(resolve_from_task_input("parabricks", payload, regulatory=regulatory))
    elif project_path:
        from methyl_utils import load_project

        project_file = Path(str(project_path)).expanduser().resolve()
        if project_file.is_file():
            project = load_project(str(project_file))
            step_cfg = dict(resolve_for_project("parabricks", project))

    engine_raw = _pick(payload, step_cfg, "engine", "engine")
    if engine_raw is None:
        engine_raw = os.environ.get("METHYL_PARABRICKS_ENGINE", "parabricks")
    engine = str(engine_raw).strip().lower() or "parabricks"
    if engine not in {"parabricks", "mojo"}:
        raise RuntimeError(
            f"actionConfig.parabricks.engine must be 'parabricks' or 'mojo' (got {engine_raw!r})"
        )

    device_raw = _pick(payload, step_cfg, "alignDevice", "align_device")
    if device_raw is None:
        device_raw = os.environ.get("METHYLGRAPHER_ALIGN_DEVICE", "auto")
    align_device = str(device_raw).strip().lower() or "auto"

    if engine == "mojo":
        from methyl_worker.mojo_align_env import image_pin as mojo_align_image_pin

        image = (
            parabricks_image
            or _pick(payload, step_cfg, "mojoImage", "mojo_image")
            or _pick(payload, step_cfg, "parabricksImage", "image")
            or mojo_align_image_pin()
            or DEFAULT_MOJO_IMAGE
        ).strip()
    else:
        image = (
            parabricks_image
            or _pick(payload, step_cfg, "parabricksImage", "image")
            or os.environ.get("METHYL_PARABRICKS_IMAGE", "")
        ).strip()
        if not image:
            raise RuntimeError(
                "Parabricks image is required when engine=parabricks "
                "(resolvedConfig.image or METHYL_PARABRICKS_IMAGE)"
            )

    gpu_raw = _pick(payload, step_cfg, "gpuFlags", "gpu_flags")
    if gpu_raw is None:
        if engine == "mojo" and align_device == "cpu":
            # GPU-less hosts: no docker device flags (prereq check also skips).
            gpu_raw = os.environ.get("METHYL_MOJO_GPU_FLAGS", "")
        elif engine == "mojo" and align_device in {"amd", "hip", "rocm"}:
            gpu_raw = os.environ.get(
                "METHYL_MOJO_GPU_FLAGS",
                "--device=/dev/kfd --device=/dev/dri --group-add video",
            )
        else:
            gpu_raw = os.environ.get("METHYL_PARABRICKS_GPU_FLAGS", "--gpus all")
    gpu_flags = tuple(shlex.split(str(gpu_raw).strip())) if str(gpu_raw).strip() else ()

    threads = bwa_threads
    if threads is None:
        threads = _pick_int(payload, step_cfg, "bwaThreads", "bwa_threads")
    if threads is None:
        threads = int(os.environ.get("METHYL_PARABRICKS_BWA_THREADS", "16"))

    extra_raw = _pick(payload, step_cfg, "extraDockerArgs", "extra_docker_args")
    if extra_raw is None:
        extra_raw = os.environ.get("METHYL_PARABRICKS_EXTRA_DOCKER_ARGS", "")
    if isinstance(extra_raw, list):
        extra_docker_args = tuple(str(x) for x in extra_raw)
    else:
        extra_docker_args = tuple(shlex.split(str(extra_raw).strip())) if str(extra_raw).strip() else ()

    cleanup_default = os.environ.get("METHYL_PARABRICKS_CLEANUP_TMP", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    cleanup_tmp = _pick_bool(payload, step_cfg, "cleanupTmp", "cleanup_tmp", cleanup_default)
    write_methylation_tags = _pick_bool(
        payload, step_cfg, "writeMethylationTags", "write_methylation_tags", False
    )

    return ParabricksConfig(
        image=image,
        gpu_flags=gpu_flags,
        bwa_threads=threads,
        extra_docker_args=extra_docker_args,
        cleanup_tmp=cleanup_tmp,
        engine=engine,
        align_device=align_device,
        write_methylation_tags=write_methylation_tags,
    )


def _load_config(
    *,
    project_path: str | Path | None = None,
    input_json: Optional[Mapping[str, Any]] = None,
    parabricks_image: Optional[str] = None,
    bwa_threads: Optional[int] = None,
) -> ParabricksConfig:
    return resolve_parabricks_config(
        project_path=project_path,
        input_json=input_json,
        parabricks_image=parabricks_image,
        bwa_threads=bwa_threads,
    )


def _resolve_paths(sample_dir: Path, sample_id: str, reference_fasta: Path) -> ParabricksPaths:
    return ParabricksPaths(
        sample_dir=sample_dir,
        sample_id=sample_id,
        reference_fasta=reference_fasta.resolve(),
        bam_path=sample_dir / f"{sample_id}.bam",
        qc_metrics_dir=sample_dir / f"{sample_id}.qc-metrics",
        qc_metrics_tar=sample_dir / f"{sample_id}.qc-metrics.tar",
        metrics_json=sample_dir / f"{sample_id}.json",
        dedup_metrics=sample_dir / f"{sample_id}.deduplicate_metrics.txt",
        log_path=sample_dir / f"{sample_id}.fq2bam_meth.log",
        tmp_dir=sample_dir / "tmp",
    )


def _in_fq_flags(container_paths: Sequence[str]) -> List[str]:
    """Clara/Parabricks ``--in-fq R1 R2`` repeated once per pair."""
    if len(container_paths) < 2 or len(container_paths) % 2:
        raise RuntimeError(
            f"fq2bam_meth --in-fq requires an even number of FASTQs, got {len(container_paths)}"
        )
    flags: List[str] = []
    for idx in range(0, len(container_paths), 2):
        flags.extend(["--in-fq", container_paths[idx], container_paths[idx + 1]])
    return flags


def _has_qc_artifact(paths: ParabricksPaths) -> bool:
    if paths.metrics_json.is_file():
        return True
    if paths.qc_metrics_tar.is_file():
        return True
    return paths.qc_metrics_dir.is_dir() and any(paths.qc_metrics_dir.iterdir())


def _clear_alignment_outputs(paths: ParabricksPaths) -> None:
    for path in (
        paths.bam_path,
        paths.metrics_json,
        paths.qc_metrics_tar,
        paths.dedup_metrics,
    ):
        if path.is_file():
            path.unlink()
    if paths.qc_metrics_dir.is_dir():
        shutil.rmtree(paths.qc_metrics_dir, ignore_errors=True)


def alignment_outputs_complete(paths: ParabricksPaths) -> bool:
    # Require a non-empty BAM so a crashed/partial write cannot skip realignment.
    return (
        paths.bam_path.is_file()
        and paths.bam_path.stat().st_size > 0
        and _has_qc_artifact(paths)
    )


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker not found on PATH; required for Parabricks fq2bam_meth")
    return docker


def _build_docker_command(
    cfg: ParabricksConfig,
    paths: ParabricksPaths,
    fastqs: Sequence[Path],
) -> List[str]:
    genome_dir = paths.reference_fasta.parent
    ref_basename = paths.reference_fasta.name
    docker_user = align_docker_user()

    in_fq_args: List[str] = []
    for fastq in fastqs:
        rel = fastq.relative_to(paths.sample_dir)
        in_fq_args.append(f"/workdir/{rel.as_posix()}")

    if cfg.engine == "mojo":
        if len(in_fq_args) != 2:
            raise RuntimeError(
                "MojoFq2bamMeth accepts one FASTQ pair; "
                f"found {len(in_fq_args)} files. Use Clara fq2bam_meth for multi-lane samples."
            )
        cmd: List[str] = [
            _docker_bin(),
            "run",
            "--rm",
            *cfg.gpu_flags,
            "--user",
            docker_user,
            "-e",
            f"METHYLGRAPHER_ALIGN_DEVICE={cfg.align_device}",
            "-e",
            f"METHYLGRAPHER_GIRAFFE_DEVICE={cfg.align_device}",
            "-e",
            f"METHYLGRAPHER_WRITE_METH_TAGS={'1' if cfg.write_methylation_tags else '0'}",
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
                "methylGrapher",
                "MojoFq2bamMeth",
                "-fq1",
                in_fq_args[0],
                "-fq2",
                in_fq_args[1],
                "-ref",
                f"/genomes/{ref_basename}",
                "-out_bam",
                f"/outputdir/{paths.bam_path.name}",
                "-out_qc_dir",
                f"/outputdir/{paths.qc_metrics_dir.name}",
                "-sample_id",
                paths.sample_id,
                "-t",
                str(cfg.bwa_threads),
                "-device",
                cfg.align_device,
                "-work_dir",
                f"/outputdir/{paths.tmp_dir.name}",
            ),
        ]
        return cmd

    cmd = [
        _docker_bin(),
        "run",
        "--rm",
        *cfg.gpu_flags,
        "--user",
        docker_user,
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
            "fq2bam_meth",
            f"--ref=/genomes/{ref_basename}",
            *_in_fq_flags(in_fq_args),
            f"--out-bam=/outputdir/{paths.bam_path.name}",
            f"--out-qc-metrics-dir=/outputdir/{paths.qc_metrics_dir.name}",
            f"--out-duplicate-metrics=/outputdir/{paths.dedup_metrics.name}",
            f"--logfile=/outputdir/{paths.log_path.name}",
            f"--tmp-dir=/outputdir/{paths.tmp_dir.name}",
            f"--bwa-cpu-thread-pool={cfg.bwa_threads}",
            "--gpusort",
            "--gpuwrite",
        ),
    ]
    return cmd


def _append_log(log_path: Path, text: str) -> None:
    append_work_text(log_path, text)


def _package_qc_metrics(paths: ParabricksPaths) -> Optional[Path]:
    if paths.qc_metrics_tar.is_file():
        return paths.qc_metrics_tar
    if not paths.qc_metrics_dir.is_dir():
        return None
    if not any(paths.qc_metrics_dir.iterdir()):
        return None

    with tarfile.open(paths.qc_metrics_tar, "w") as tar:
        tar.add(paths.qc_metrics_dir, arcname=paths.qc_metrics_dir.name)
    return paths.qc_metrics_tar


def _cleanup_tmp(paths: ParabricksPaths) -> None:
    if paths.tmp_dir.is_dir():
        shutil.rmtree(paths.tmp_dir, ignore_errors=True)


def _reset_tmp(paths: ParabricksPaths) -> None:
    """Empty leftover Align ``tmp`` and leave it other-writable for Docker.

    ``pbrun`` / ``MojoFq2bamMeth`` create children under ``/outputdir/tmp``.
    Align Docker uses ``--user 1000:1000`` (often not this host's ``ubuntu``
    uid) and NFS ``root_squash`` maps Clara-as-root to ``nobody``. A fresh
    umask-022 ``0755`` dir owned by the worker is not writable to either.
    Keep an already other-writable leftover (umask ``000``) instead of
    replacing it; always ``share_work_tree`` the directory that remains.
    """
    if paths.tmp_dir.exists():
        share_work_tree(paths.tmp_dir)
    if paths.tmp_dir.is_file():
        paths.tmp_dir.unlink()
    elif paths.tmp_dir.is_dir():
        for child in list(paths.tmp_dir.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)
    share_work_tree(paths.tmp_dir)


def run_fq2bam_meth(
    *,
    sample_id: str,
    sample_dir: str | Path,
    reference_fasta: str | Path,
    project: str | Path | None = None,
    input_json: Optional[Mapping[str, Any]] = None,
    parabricks_image: Optional[str] = None,
    bwa_threads: Optional[int] = None,
) -> Dict[str, Optional[str]]:
    """Align bisulfite FASTQs (Clara fq2bam_meth or MojoFq2bamMeth) in Docker."""
    sample_path = Path(sample_dir).resolve()
    reference_path = Path(reference_fasta).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")
    if not reference_path.is_file():
        raise RuntimeError(f"referenceFasta not found: {reference_path}")

    cfg = _load_config(
        project_path=project or (input_json or {}).get("projectPath") or (input_json or {}).get("project"),
        input_json=input_json,
        parabricks_image=parabricks_image,
        bwa_threads=bwa_threads,
    )
    paths = _resolve_paths(sample_path, sample_id, reference_path)

    force_realign = _pick_bool(input_json or {}, {}, "forceRealign", "forceRealign", default=False)
    if force_realign and alignment_outputs_complete(paths):
        logger.info("forceRealign: clearing existing alignment outputs for %s", sample_id)
        _clear_alignment_outputs(paths)

    if alignment_outputs_complete(paths):
        logger.info("Skipping linear Align; outputs already present for %s", sample_id)
        _package_qc_metrics(paths)
        return _result_payload(paths)

    from methyl_worker.capabilities import assert_execute_gpu_prereqs

    # Mojo cpu device does not require a GPU; Clara always does.
    if cfg.engine == "parabricks" or cfg.align_device not in {"cpu"}:
        assert_execute_gpu_prereqs("parabricks.fq2bam", "sample.parabricks_fq2bam")

    fastqs = resolve_paired_fastqs(sample_path, sample_id)
    docker_cmd = _build_docker_command(cfg, paths, fastqs)
    paths.sample_dir.mkdir(parents=True, exist_ok=True)
    _reset_tmp(paths)

    label = "MojoFq2bamMeth" if cfg.engine == "mojo" else "Parabricks fq2bam_meth"
    logger.info("Running %s for %s (device=%s)", label, sample_id, cfg.align_device)
    _append_log(paths.log_path, "COMMAND: " + " ".join(shlex.quote(part) for part in docker_cmd))

    proc = subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout:
        _append_log(paths.log_path, proc.stdout)
    if proc.stderr:
        _append_log(paths.log_path, proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or f"{label} failed"
        )

    if not paths.bam_path.is_file():
        raise RuntimeError(f"{label} did not produce BAM: {paths.bam_path}")

    tar_path = _package_qc_metrics(paths)
    if tar_path is None and not paths.metrics_json.is_file():
        raise RuntimeError(
            f"{label} did not produce QC metrics for {sample_id}: expected "
            f"{paths.qc_metrics_dir} or {paths.qc_metrics_tar}"
        )

    if cfg.cleanup_tmp:
        _cleanup_tmp(paths)

    return _result_payload(paths)


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
    parser = argparse.ArgumentParser(description="Run Parabricks fq2bam_meth via Docker")
    parser.add_argument("sample_id", help="Sample identifier")
    parser.add_argument(
        "--sample-dir",
        help="Sample working directory (default: /work/samples/{sample_id})",
    )
    parser.add_argument(
        "--reference-fasta",
        help="Reference FASTA path (default: METHYL_REFERENCE_FASTA or GRCh38 under genomes dir)",
    )
    parser.add_argument("--image", help="Override METHYL_PARABRICKS_IMAGE")
    parser.add_argument("--bwa-threads", type=int, help="Override METHYL_PARABRICKS_BWA_THREADS")
    args = parser.parse_args(list(argv) if argv is not None else None)

    sample_dir = Path(
        args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}"
    )
    reference = args.reference_fasta or os.environ.get("METHYL_REFERENCE_FASTA")
    if not reference:
        genomes_dir = Path(
            os.environ.get(
                "METHYL_GENOMES_DIR",
                "/work/genomes/linear/GRCh38/ensembl-116",
            )
        )
        reference = str(genomes_dir / "Homo_sapiens.GRCh38.dna.primary_assembly.fa")

    result = run_fq2bam_meth(
        sample_id=args.sample_id,
        sample_dir=sample_dir,
        reference_fasta=reference,
        parabricks_image=args.image,
        bwa_threads=args.bwa_threads,
    )
    for key, value in result.items():
        if value is not None:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
