"""Prosit deep-learning rescoring via Docker (GPU), open-source.

Runs a Prosit/MS2Rescore container (own ``METHYL_PROSIT_IMAGE`` env) to rescore a DIA-NN
report and lift IDs at fixed FDR. GPU-native (PyTorch); ideal for GH200. The image must be
linux/arm64 (or multi-arch). Model weights should be pinned as a cfg reference_asset.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker not found on PATH; required for Prosit rescoring")
    return docker


def _resolve_image() -> str:
    image = os.environ.get("METHYL_PROSIT_IMAGE", "").strip()
    if not image:
        raise RuntimeError("Prosit image is required (METHYL_PROSIT_IMAGE)")
    return image


def _gpu_flags() -> List[str]:
    raw = os.environ.get("METHYL_PROSIT_GPU_FLAGS", "--gpus all")
    return shlex.split(raw.strip()) if raw.strip() else ["--gpus", "all"]


def run_prosit_rescore(
    *,
    sample_id: str,
    sample_dir: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """Rescore a DIA-NN report with Prosit (GPU Docker), writing a rescored report."""
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")
    report = sample_path / f"{sample_id}.diann" / "report.tsv"
    if not report.is_file():
        raise RuntimeError(f"Prosit rescoring requires a DIA-NN report: {report}")

    out_dir = sample_path / f"{sample_id}.prosit"
    out_dir.mkdir(parents=True, exist_ok=True)
    uid, gid = os.getuid(), os.getgid()
    extra = shlex.split(os.environ.get("METHYL_PROSIT_EXTRA_DOCKER_ARGS", "").strip() or "")
    cmd: List[str] = [
        _docker_bin(),
        "run",
        "--rm",
        *_gpu_flags(),
        "--user",
        f"{uid}:{gid}",
        "-v",
        f"{sample_path}:/workdir",
        "-w",
        "/workdir",
        *extra,
        _resolve_image(),
        "ms2rescore",
        "--psm-file",
        f"/workdir/{sample_id}.diann/report.tsv",
        "--output-path",
        f"/workdir/{out_dir.name}",
    ]
    log_path = sample_path / f"{sample_id}.prosit.log"
    log_path.write_text("COMMAND: " + " ".join(shlex.quote(p) for p in cmd) + "\n", encoding="utf-8")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(proc.stdout or "")
        handle.write(proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Prosit rescoring failed")
    rescored = out_dir / "report.rescored.tsv"
    return {
        "sampleId": sample_id,
        "reportTsv": str(rescored if rescored.is_file() else report),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Prosit/MS2Rescore via Docker (GPU)")
    parser.add_argument("sample_id")
    parser.add_argument("--sample-dir")
    args = parser.parse_args(list(argv) if argv is not None else None)
    sample_dir = Path(args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}")
    result = run_prosit_rescore(sample_id=args.sample_id, sample_dir=sample_dir)
    for key, value in result.items():
        if value is not None:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
