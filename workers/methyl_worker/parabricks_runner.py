"""Linear WGBS Align: Clara Parabricks fq2bam_meth or portable MojoFq2bamMeth."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from methyl_utils.local_align import (
    DEFAULT_LOCAL_TMP_ROOT,
    local_bam_path,
    local_tmp_root,
    remove_local_align,
    sample_scratch_name,
)
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
# goliath-worker installs Clara here, outside the default PATH.
_DEFAULT_PBRUN = Path("/usr/local/parabricks/pbrun")
# pbrun deleteTmpDir waits forever on geesefs. Keep scratch on local disk.
_DEFAULT_PB_TMP_ROOT = DEFAULT_LOCAL_TMP_ROOT
# Bind-mount target inside the Parabricks/Mojo container (not /outputdir).
_CONTAINER_PB_TMP = "/tmp/methyl-pb"
# Worker-local BAM directory inside the container (never the /work mount).
_CONTAINER_BAM_DIR = "/bamdir"
# A WGBS sample can take many hours. A hang must still end the task.
_DEFAULT_PBRUN_TIMEOUT_SEC = 48 * 60 * 60


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
        if use_host_pbrun():
            # Host image already has pbrun; docker image tag is unused.
            clara = "host-pbrun"
        else:
            raise RuntimeError(
                "collectmultiplemetrics requires METHYL_PARABRICKS_IMAGE "
                "(Clara Parabricks image with pbrun) or host pbrun on PATH; "
                "Mojo methylgrapher images do not ship pbrun"
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


def _local_pbrun_tmp_root() -> Path:
    """Directory that holds per-task Parabricks scratch, never the /work mount.

    ``METHYL_LOCAL_TMP`` wins. Otherwise a ``methyl-pb`` directory under
    ``TMPDIR``. The default is ``/tmp/methyl-pb``.
    """
    return local_tmp_root()


def _local_pbrun_tmp_dir(sample_id: str, sample_dir: Path) -> Path:
    """One local scratch directory for this sample's align task."""
    return _local_pbrun_tmp_root() / sample_scratch_name(sample_id, sample_dir)


def _pbrun_timeout_seconds() -> float:
    """Seconds ``pbrun`` may run before the worker kills it.

    ``METHYL_PBRUN_TIMEOUT_SEC`` overrides the default (48 hours).
    """
    raw = os.environ.get("METHYL_PBRUN_TIMEOUT_SEC", "").strip()
    if not raw:
        return float(_DEFAULT_PBRUN_TIMEOUT_SEC)
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"METHYL_PBRUN_TIMEOUT_SEC must be a positive number of seconds, got {raw!r}"
        ) from exc
    if value <= 0:
        raise RuntimeError(
            f"METHYL_PBRUN_TIMEOUT_SEC must be a positive number of seconds, got {raw!r}"
        )
    return value


def _subprocess_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


def _resolve_paths(sample_dir: Path, sample_id: str, reference_fasta: Path) -> ParabricksPaths:
    return ParabricksPaths(
        sample_dir=sample_dir,
        sample_id=sample_id,
        reference_fasta=reference_fasta.resolve(),
        # Worker-local; QC / fragmentomics / extract / delete_bam are pinned here.
        bam_path=local_bam_path(sample_id, sample_dir),
        qc_metrics_dir=sample_dir / f"{sample_id}.qc-metrics",
        qc_metrics_tar=sample_dir / f"{sample_id}.qc-metrics.tar",
        metrics_json=sample_dir / f"{sample_id}.json",
        dedup_metrics=sample_dir / f"{sample_id}.deduplicate_metrics.txt",
        log_path=sample_dir / f"{sample_id}.fq2bam_meth.log",
        tmp_dir=_local_pbrun_tmp_dir(sample_id, sample_dir),
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
    remove_local_align(paths.sample_id, paths.sample_dir)
    for path in (
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


def resolve_pbrun() -> Optional[str]:
    """Host ``pbrun``, or None when Clara is not installed on this machine.

    ``METHYL_PBRUN_BIN`` wins. Otherwise ``PATH``, then the goliath-worker
    image path ``/usr/local/parabricks/pbrun``.
    """
    explicit = os.environ.get("METHYL_PBRUN_BIN", "").strip()
    if explicit:
        return explicit
    found = shutil.which("pbrun")
    if found:
        return found
    if _DEFAULT_PBRUN.is_file():
        return str(_DEFAULT_PBRUN)
    return None


def _host_pbrun_argv0() -> str:
    return resolve_pbrun() or "pbrun"


def use_host_pbrun() -> bool:
    """True when Clara ``pbrun`` should run on the worker host (no nested docker).

    ``METHYL_PARABRICKS_MODE=host|native`` forces host mode (requires ``pbrun``).
    ``docker|container`` forces docker. Default ``auto``: host when ``pbrun``
    resolves (``PATH``, ``METHYL_PBRUN_BIN``, or ``/usr/local/parabricks/pbrun``),
    else docker (SSH VMs that still pull NGC).
    """
    mode = os.environ.get("METHYL_PARABRICKS_MODE", "auto").strip().lower()
    has_pbrun = resolve_pbrun() is not None
    if mode in {"host", "native", "1", "true", "yes"}:
        if not has_pbrun:
            raise RuntimeError(
                "METHYL_PARABRICKS_MODE=host but pbrun is not on PATH; "
                "use the goliath-worker image or install Clara Parabricks"
            )
        return True
    if mode in {"docker", "container"}:
        return False
    return has_pbrun



def use_host_methylgrapher() -> bool:
    """True when ``methylGrapher`` / GoliathAlign should run on the host (no nested docker).

    ``METHYL_METHYLGRAPHER_MODE=host|native`` forces host. ``docker|container``
    forces docker. Default ``auto``: host when the CLI is resolvable (boot
    installs the jammy release tarball via goliath_align_local.sh).
    """
    mode = os.environ.get("METHYL_METHYLGRAPHER_MODE", "auto").strip().lower()
    bin_name = os.environ.get("METHYL_METHYLGRAPHER_BIN", "").strip() or "methylGrapher"
    has = shutil.which(bin_name) is not None
    if not has:
        home = os.environ.get("GOLIATH_ALIGN_HOME", "").strip()
        candidates = []
        if home:
            candidates.append(Path(home) / "bin" / "methylGrapher")
        candidates.extend(
            [
                Path("/usr/local/bin/methylGrapher"),
                Path("/opt/goliath-align/bin/methylGrapher"),
                Path("/opt/goliath/goliath-align-amd64/bin/methylGrapher"),
            ]
        )
        for cand in candidates:
            if cand.is_file():
                has = True
                break
    if mode in {"host", "native", "1", "true", "yes"}:
        if not has:
            raise RuntimeError(
                "METHYL_METHYLGRAPHER_MODE=host but methylGrapher is not on PATH; "
                "the release must include goliath-align-host-linux-amd64-jammy.tar.gz "
                "and start_shared_worker must install it (goliath_align_local.sh)"
            )
        return True
    if mode in {"docker", "container"}:
        return False
    return has


def _methylgrapher_bin() -> str:
    explicit = os.environ.get("METHYL_METHYLGRAPHER_BIN", "").strip()
    if explicit:
        return explicit
    found = shutil.which("methylGrapher")
    if found:
        return found
    for cand in (
        Path("/usr/local/bin/methylGrapher"),
        Path("/opt/goliath-align/bin/methylGrapher"),
        Path("/work/goliath/opt/goliath-align/bin/methylGrapher"),
        Path("/work/goliath/opt/goliath-align/methylGrapher.mojo.sh"),
    ):
        if cand.is_file():
            return str(cand)
    return "methylGrapher"



def _build_host_fq2bam_meth_command(
    cfg: ParabricksConfig,
    paths: ParabricksPaths,
    fastqs: Sequence[Path],
) -> List[str]:
    host_fqs = [str(fq.resolve()) for fq in fastqs]
    return [
        _host_pbrun_argv0(),
        "fq2bam_meth",
        f"--ref={paths.reference_fasta.resolve()}",
        *_in_fq_flags(host_fqs),
        f"--out-bam={paths.bam_path.resolve()}",
        f"--out-qc-metrics-dir={paths.qc_metrics_dir.resolve()}",
        f"--out-duplicate-metrics={paths.dedup_metrics.resolve()}",
        f"--logfile={paths.log_path.resolve()}",
        f"--tmp-dir={paths.tmp_dir.resolve()}",
        f"--bwa-cpu-thread-pool={cfg.bwa_threads}",
        "--gpusort",
        "--gpuwrite",
    ]


def build_collectmultiplemetrics_command(
    cfg: ParabricksConfig,
    paths: ParabricksPaths,
    reference_fasta: Path,
) -> List[str]:
    """Host ``pbrun`` or docker ``pbrun collectmultiplemetrics``."""
    ref = reference_fasta.resolve()
    if use_host_pbrun():
        return [
            _host_pbrun_argv0(),
            "collectmultiplemetrics",
            f"--ref={ref}",
            f"--bam={paths.bam_path.resolve()}",
            f"--out-qc-metrics-dir={paths.qc_metrics_dir.resolve()}",
            "--gen-all-metrics",
        ]
    genome_dir = ref.parent
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
        *_docker_bam_volume(paths),
        "-v",
        f"{genome_dir}:/genomes:ro",
        "-w",
        "/workdir",
        *cfg.extra_docker_args,
        *docker_umask_prefix(),
        cfg.image,
        *docker_umask_suffix(
            "pbrun",
            "collectmultiplemetrics",
            f"--ref=/genomes/{ref.name}",
            f"--bam={_CONTAINER_BAM_DIR}/{paths.bam_path.name}",
            f"--out-qc-metrics-dir=/outputdir/{paths.qc_metrics_dir.name}",
            "--gen-all-metrics",
        ),
    ]


def _build_host_mojo_fq2bam_meth_command(
    cfg: ParabricksConfig,
    paths: ParabricksPaths,
    fastqs: Sequence[Path],
) -> List[str]:
    if len(fastqs) != 2:
        raise RuntimeError(
            "MojoFq2bamMeth accepts one FASTQ pair; "
            f"found {len(fastqs)} files. Use Clara fq2bam_meth for multi-lane samples."
        )
    mg = _methylgrapher_bin()
    return [
        mg,
        "MojoFq2bamMeth",
        "-fq1",
        str(fastqs[0].resolve()),
        "-fq2",
        str(fastqs[1].resolve()),
        "-ref",
        str(paths.reference_fasta.resolve()),
        "-out_bam",
        str(paths.bam_path.resolve()),
        "-out_qc_dir",
        str(paths.qc_metrics_dir.resolve()),
        "-sample_id",
        paths.sample_id,
        "-t",
        str(cfg.bwa_threads),
        "-device",
        cfg.align_device,
        "-work_dir",
        str(paths.tmp_dir.resolve()),
    ]


def _docker_container_name(paths: ParabricksPaths) -> str:
    """Stable ``docker run --name`` so a killed CLI can still drop the GPU container.

    The same name is removed before the next ``docker run``, so a leftover from
    a crash or a failed ``docker rm`` does not block the retry.
    """
    return f"methyl-pb-{paths.tmp_dir.name}"


def _docker_tmp_volume(paths: ParabricksPaths) -> List[str]:
    """Bind the local scratch dir into the container, off the /work mount."""
    return ["-v", f"{paths.tmp_dir.resolve()}:{_CONTAINER_PB_TMP}"]


def _docker_bam_volume(paths: ParabricksPaths) -> List[str]:
    """Bind the worker-local BAM directory into the container."""
    return ["-v", f"{paths.bam_path.parent.resolve()}:{_CONTAINER_BAM_DIR}"]


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
            "--name",
            _docker_container_name(paths),
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
            *_docker_tmp_volume(paths),
            *_docker_bam_volume(paths),
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
                f"{_CONTAINER_BAM_DIR}/{paths.bam_path.name}",
                "-out_qc_dir",
                f"/outputdir/{paths.qc_metrics_dir.name}",
                "-sample_id",
                paths.sample_id,
                "-t",
                str(cfg.bwa_threads),
                "-device",
                cfg.align_device,
                "-work_dir",
                _CONTAINER_PB_TMP,
            ),
        ]
        return cmd

    cmd = [
        _docker_bin(),
        "run",
        "--rm",
        *cfg.gpu_flags,
        "--name",
        _docker_container_name(paths),
        "--user",
        docker_user,
        "-v",
        f"{paths.sample_dir.resolve()}:/workdir",
        "-v",
        f"{paths.sample_dir.resolve()}:/outputdir",
        *_docker_tmp_volume(paths),
        *_docker_bam_volume(paths),
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
            f"--out-bam={_CONTAINER_BAM_DIR}/{paths.bam_path.name}",
            f"--out-qc-metrics-dir=/outputdir/{paths.qc_metrics_dir.name}",
            f"--out-duplicate-metrics=/outputdir/{paths.dedup_metrics.name}",
            f"--logfile=/outputdir/{paths.log_path.name}",
            f"--tmp-dir={_CONTAINER_PB_TMP}",
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


def _reset_local_bam_dir(paths: ParabricksPaths) -> None:
    """Fresh, other-writable worker-local BAM dir (docker runs as a non-root user)."""
    remove_local_align(paths.sample_id, paths.sample_dir)
    bam_dir = paths.bam_path.parent
    bam_dir.mkdir(parents=True, exist_ok=True)
    share_work_tree(bam_dir)


def _reset_tmp(paths: ParabricksPaths) -> None:
    """Empty leftover Align scratch on local disk and leave it other-writable.

    ``paths.tmp_dir`` is under ``METHYL_LOCAL_TMP`` or ``TMPDIR`` (default
    ``/tmp/methyl-pb``), not on geesefs. ``pbrun deleteTmpDir`` hangs in the
    kernel when that tree is on ``/work``. Docker bind-mounts this directory
    at ``/tmp/methyl-pb`` and runs as ``--user 1000:1000``, so the directory
    stays other-writable. Keep an already other-writable leftover instead of
    replacing it.
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


_BWAMETH_C2T_SUFFIXES = (
    ".bwameth.c2t",
    ".bwameth.c2t.amb",
    ".bwameth.c2t.ann",
    ".bwameth.c2t.bwt",
    ".bwameth.c2t.pac",
    ".bwameth.c2t.sa",
)


def bwameth_c2t_index_paths(reference_fasta: str | Path) -> tuple[Path, ...]:
    """The six bwa-meth C2T FM-index siblings ``pbrun fq2bam_meth`` requires."""
    base = str(reference_fasta)
    return tuple(Path(base + suffix) for suffix in _BWAMETH_C2T_SUFFIXES)


def _docker_run_name(cmd: Sequence[str]) -> Optional[str]:
    """``docker run --name`` value, when this argv starts a container."""
    if len(cmd) < 2 or Path(cmd[0]).name != "docker" or cmd[1] != "run":
        return None
    try:
        index = cmd.index("--name")
    except ValueError:
        return None
    if index + 1 >= len(cmd):
        return None
    return cmd[index + 1]


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGKILL the session from ``start_new_session`` (leader and children).

    ``subprocess.run(..., timeout=)`` signals only the direct child. Host
    ``pbrun`` workers and a ``docker`` CLI's grandchildren keep the GPU.
    """
    if not proc.pid:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _force_remove_docker_container(docker_bin: str, name: str, log_path: Path) -> None:
    """Drop a ``--gpus`` container that outlived the ``docker run`` CLI."""
    try:
        completed = subprocess.run(
            [docker_bin, "rm", "-f", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _append_log(log_path, f"docker rm -f {name} failed: {exc}")
        return
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        _append_log(
            log_path,
            f"docker rm -f {name} failed: {detail or completed.returncode}",
        )


def _close_align_pipes(proc: subprocess.Popen) -> None:
    for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _teardown_align_process(proc: subprocess.Popen, cmd: Sequence[str], log_path: Path) -> None:
    """SIGKILL the align session and drop a named GPU container if one was started."""
    _kill_process_group(proc)
    container_name = _docker_run_name(cmd)
    if container_name:
        _force_remove_docker_container(cmd[0], container_name, log_path)


def _run_align_subprocess(
    cmd: List[str],
    *,
    log_path: Path,
    label: str,
) -> subprocess.CompletedProcess[str]:
    """Run pbrun (or the docker wrapper) and fail clearly when it exceeds the timeout.

    The child is a session leader. A named ``docker run`` container is removed
    before start so a leftover name cannot block the retry. If ``communicate``
    does not return — timeout or any other error — the whole group is SIGKILLed
    and that container is removed again before any ``/work`` log append. Those
    appends chmod and can block, so they must not run while the GPU session is
    still alive.
    """
    timeout = _pbrun_timeout_seconds()
    container_name = _docker_run_name(cmd)
    if container_name:
        _force_remove_docker_container(cmd[0], container_name, log_path)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    finished = False
    stdout = ""
    stderr = ""
    timeout_error: Optional[subprocess.TimeoutExpired] = None
    communicate_error: Optional[Exception] = None
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            finished = True
        except subprocess.TimeoutExpired as exc:
            timeout_error = exc
            stdout = _subprocess_text(exc.stdout)
            stderr = _subprocess_text(exc.stderr)
        except Exception as exc:
            communicate_error = exc
    finally:
        if not finished:
            _teardown_align_process(proc, cmd, log_path)
        _close_align_pipes(proc)

    if timeout_error is not None:
        if stdout:
            _append_log(log_path, stdout)
        if stderr:
            _append_log(log_path, stderr)
        message = (
            f"{label} timed out after {timeout:g}s "
            "(METHYL_PBRUN_TIMEOUT_SEC) and was killed"
        )
        _append_log(log_path, message)
        raise RuntimeError(message) from timeout_error
    if communicate_error is not None:
        _append_log(
            log_path,
            f"{label} failed before exit ({type(communicate_error).__name__}: {communicate_error}); stopping the align session",
        )
        raise communicate_error
    return subprocess.CompletedProcess(
        cmd, int(proc.returncode or 0), stdout or "", stderr or ""
    )


def require_bwameth_c2t_index(reference_fasta: str | Path) -> None:
    """Fail before ``pbrun fq2bam_meth`` when any C2T sibling is missing."""
    missing = [path for path in bwameth_c2t_index_paths(reference_fasta) if not path.is_file()]
    if not missing:
        return
    listed = ", ".join(str(path) for path in missing)
    raise RuntimeError(f"bwa-meth C2T index missing for {reference_fasta}: {listed}")


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
    """Align bisulfite FASTQs (Clara fq2bam_meth or MojoFq2bamMeth; host or docker)."""
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
    if cfg.engine == "parabricks":
        require_bwameth_c2t_index(reference_path)
    if cfg.engine == "mojo" and use_host_methylgrapher():
        align_cmd = _build_host_mojo_fq2bam_meth_command(cfg, paths, fastqs)
        label = "MojoFq2bamMeth (host)"
    elif cfg.engine == "parabricks" and use_host_pbrun():
        align_cmd = _build_host_fq2bam_meth_command(cfg, paths, fastqs)
        label = "Parabricks fq2bam_meth (host pbrun)"
    else:
        align_cmd = _build_docker_command(cfg, paths, fastqs)
        label = "MojoFq2bamMeth (docker)" if cfg.engine == "mojo" else "Parabricks fq2bam_meth (docker)"
    paths.sample_dir.mkdir(parents=True, exist_ok=True)
    _reset_tmp(paths)
    _reset_local_bam_dir(paths)

    succeeded = False
    try:
        logger.info("Running %s for %s (device=%s)", label, sample_id, cfg.align_device)
        _append_log(paths.log_path, "COMMAND: " + " ".join(shlex.quote(part) for part in align_cmd))

        proc = _run_align_subprocess(align_cmd, log_path=paths.log_path, label=label)
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

        succeeded = True
        return _result_payload(paths)
    finally:
        if not succeeded:
            # A failed align must not leave a partial BAM on the worker.
            remove_local_align(paths.sample_id, paths.sample_dir)
        # Timeout raises before a successful BAM/QC write. The hang this limit
        # exists for is deleteTmpDir, so the local scratch tree is still full.
        if cfg.cleanup_tmp:
            _cleanup_tmp(paths)


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
