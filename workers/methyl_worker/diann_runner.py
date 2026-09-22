"""GPU-accelerated DIA-NN mass-spec quantification via Docker.

Parallel to :mod:`methyl_worker.rna_fq2bam_runner`, but for proteomics DIA data and
**not** Parabricks: it runs a DIA-NN container (its own ``METHYL_DIANN_IMAGE`` env, its
own ``--gpus`` flags) so it can coexist with Parabricks on the same GH200 nodes. The
image must be linux/arm64 (or multi-arch) for Grace/Hopper hosts (no ``--platform`` pin).
Emits a DIA-NN ``report.tsv`` that ``register_abundance`` normalizes to the shared
abundance contract.
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

logger = logging.getLogger(__name__)

MSDATA_SUFFIXES: Sequence[str] = (".raw", ".mzml", ".mzML", ".d", ".dia", ".wiff")


@dataclass(frozen=True)
class DiannConfig:
    image: str
    gpu_flags: tuple
    threads: int
    extra_docker_args: tuple


def resolve_diann_config(input_json: Optional[Mapping[str, Any]] = None) -> DiannConfig:
    payload = dict(input_json or {})
    step: Dict[str, Any] = {}
    from methyl_utils.action_config_resolver import resolve_from_task_input

    if payload:
        step = dict(resolve_from_task_input("proteomics_quant", payload))

    image = (
        payload.get("diannImage")
        or step.get("image")
        or os.environ.get("METHYL_DIANN_IMAGE", "")
    ).strip()
    if not image:
        raise RuntimeError(
            "DIA-NN image is required (task diannImage, actionConfig.proteomics_quant.image, "
            "or METHYL_DIANN_IMAGE)"
        )
    gpu_raw = os.environ.get("METHYL_DIANN_GPU_FLAGS", "--gpus all")
    gpu_flags = tuple(shlex.split(gpu_raw.strip())) if gpu_raw.strip() else ("--gpus", "all")
    threads = int(step.get("threads") or os.environ.get("METHYL_DIANN_THREADS", "8"))
    extra_raw = os.environ.get("METHYL_DIANN_EXTRA_DOCKER_ARGS", "")
    extra = tuple(shlex.split(extra_raw.strip())) if extra_raw.strip() else ()
    return DiannConfig(image=image, gpu_flags=gpu_flags, threads=threads, extra_docker_args=extra)


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker not found on PATH; required for DIA-NN")
    return docker


def _collect_msdata(sample_dir: Path) -> List[Path]:
    found: List[Path] = []
    for suffix in MSDATA_SUFFIXES:
        found.extend(sorted(sample_dir.glob(f"*{suffix}")))
    # .d is a directory (Bruker); include dirs too
    for d in sorted(sample_dir.glob("*.d")):
        if d.is_dir() and d not in found:
            found.append(d)
    return sorted(set(found))


def _resolve_reference(input_json: Mapping[str, Any], site_path: str | Path | None) -> Dict[str, str]:
    from methyl_utils.action_config_resolver import (
        load_site_manifest,
        resolve_from_task_input,
        resolve_proteomics_reference,
    )

    cfg = resolve_from_task_input("proteomics_quant", dict(input_json))
    site = input_json.get("siteConfig")
    if isinstance(site, dict):
        ref = resolve_proteomics_reference(site)
    elif site_path:
        ref = resolve_proteomics_reference(load_site_manifest(site_path))
    else:
        ref = resolve_proteomics_reference()
    for key in ("protein_fasta", "spectral_library"):
        if cfg.get(key):
            ref[key] = str(cfg[key])
    return ref


def _report_path(sample_dir: Path, sample_id: str) -> Path:
    return sample_dir / f"{sample_id}.diann" / "report.tsv"


def outputs_complete(sample_dir: Path, sample_id: str) -> bool:
    return _report_path(sample_dir, sample_id).is_file()


def _build_docker_command(cfg: DiannConfig, sample_dir: Path, out_dir: Path, msdata: Sequence[Path], ref: Dict[str, str]) -> List[str]:
    uid = os.getuid()
    gid = os.getgid()
    cmd: List[str] = [
        _docker_bin(),
        "run",
        "--rm",
        *cfg.gpu_flags,
        "--user",
        f"{uid}:{gid}",
        "-v",
        f"{sample_dir.resolve()}:/workdir",
    ]
    ref_mounts: List[str] = []
    fasta = ref.get("protein_fasta")
    lib = ref.get("spectral_library")
    if fasta:
        cmd += ["-v", f"{Path(fasta).parent.resolve()}:/fasta:ro"]
        ref_mounts += ["--fasta", f"/fasta/{Path(fasta).name}"]
    if lib:
        cmd += ["-v", f"{Path(lib).parent.resolve()}:/lib:ro"]
        ref_mounts += ["--lib", f"/lib/{Path(lib).name}"]
    cmd += ["-w", "/workdir", *cfg.extra_docker_args, cfg.image, "diann"]
    for f in msdata:
        cmd += ["--f", f"/workdir/{f.name}"]
    cmd += ref_mounts
    cmd += [
        "--out",
        f"/workdir/{out_dir.name}/report.tsv",
        "--threads",
        str(cfg.threads),
        "--qvalue",
        "0.01",
    ]
    if not lib:
        # Library-free mode requires a FASTA + in-silico prediction.
        cmd += ["--predictor", "--gen-spec-lib"]
    return cmd


def run_diann(
    *,
    sample_id: str,
    sample_dir: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
    site_path: str | Path | None = None,
) -> Dict[str, Optional[str]]:
    """Quantify DIA MS data for one sample with DIA-NN (GPU Docker)."""
    payload = dict(input_json or {})
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    if outputs_complete(sample_path, sample_id):
        logger.info("Skipping DIA-NN; report already present for %s", sample_id)
        return {"sampleId": sample_id, "reportTsv": str(_report_path(sample_path, sample_id))}

    msdata = _collect_msdata(sample_path)
    if not msdata:
        raise RuntimeError(f"no MS data files under {sample_path} (expected {MSDATA_SUFFIXES})")

    cfg = resolve_diann_config(payload)
    ref = _resolve_reference(payload, site_path)
    if not ref.get("protein_fasta") and not ref.get("spectral_library"):
        raise RuntimeError(
            "DIA-NN requires site proteomics_reference.protein_fasta and/or spectral_library"
        )

    out_dir = sample_path / f"{sample_id}.diann"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = sample_path / f"{sample_id}.diann.log"
    docker_cmd = _build_docker_command(cfg, sample_path, out_dir, msdata, ref)
    log_path.write_text("COMMAND: " + " ".join(shlex.quote(p) for p in docker_cmd) + "\n", encoding="utf-8")

    proc = subprocess.run(docker_cmd, capture_output=True, text=True, check=False)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(proc.stdout or "")
        handle.write(proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "DIA-NN failed")

    report = _report_path(sample_path, sample_id)
    if not report.is_file():
        raise RuntimeError(f"DIA-NN did not produce report: {report}")
    return {"sampleId": sample_id, "reportTsv": str(report)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run DIA-NN via Docker (GPU)")
    parser.add_argument("sample_id")
    parser.add_argument("--sample-dir")
    parser.add_argument("--site-config")
    args = parser.parse_args(list(argv) if argv is not None else None)
    sample_dir = Path(args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}")
    result = run_diann(sample_id=args.sample_id, sample_dir=sample_dir, site_path=args.site_config)
    for key, value in result.items():
        if value is not None:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
