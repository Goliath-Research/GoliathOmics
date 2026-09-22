"""Casanovo de novo peptide sequencing via Docker (GPU), open-source.

Runs a Casanovo container (own ``METHYL_CASANOVO_IMAGE`` env) for de novo sequencing of
novel/variant peptides. GPU-native (PyTorch/transformer); the image must be linux/arm64
(or multi-arch). Model weights should be pinned as a cfg reference_asset.
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

MSDATA_SUFFIXES: Sequence[str] = (".mzml", ".mzML", ".mgf")


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker not found on PATH; required for Casanovo")
    return docker


def _resolve_image() -> str:
    image = os.environ.get("METHYL_CASANOVO_IMAGE", "").strip()
    if not image:
        raise RuntimeError("Casanovo image is required (METHYL_CASANOVO_IMAGE)")
    return image


def _gpu_flags() -> List[str]:
    raw = os.environ.get("METHYL_CASANOVO_GPU_FLAGS", "--gpus all")
    return shlex.split(raw.strip()) if raw.strip() else ["--gpus", "all"]


def _collect_spectra(sample_dir: Path) -> List[Path]:
    found: List[Path] = []
    for suffix in MSDATA_SUFFIXES:
        found.extend(sorted(sample_dir.glob(f"*{suffix}")))
    return sorted(set(found))


def run_casanovo(
    *,
    sample_id: str,
    sample_dir: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """De novo sequence spectra for one sample with Casanovo (GPU Docker)."""
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")
    spectra = _collect_spectra(sample_path)
    if not spectra:
        raise RuntimeError(f"no spectra (.mzML/.mgf) under {sample_path} for Casanovo")

    out = sample_path / f"{sample_id}.casanovo.mztab"
    uid, gid = os.getuid(), os.getgid()
    extra = shlex.split(os.environ.get("METHYL_CASANOVO_EXTRA_DOCKER_ARGS", "").strip() or "")
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
        "casanovo",
        "sequence",
        f"/workdir/{spectra[0].name}",
        "--output",
        f"/workdir/{out.name}",
    ]
    log_path = sample_path / f"{sample_id}.casanovo.log"
    log_path.write_text("COMMAND: " + " ".join(shlex.quote(p) for p in cmd) + "\n", encoding="utf-8")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(proc.stdout or "")
        handle.write(proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Casanovo failed")
    return {"sampleId": sample_id, "peptidesCsv": str(out) if out.is_file() else None}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Casanovo de novo via Docker (GPU)")
    parser.add_argument("sample_id")
    parser.add_argument("--sample-dir")
    args = parser.parse_args(list(argv) if argv is not None else None)
    sample_dir = Path(args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}")
    result = run_casanovo(sample_id=args.sample_id, sample_dir=sample_dir)
    for key, value in result.items():
        if value is not None:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
