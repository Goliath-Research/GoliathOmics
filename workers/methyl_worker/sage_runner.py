"""Sage DDA database search + LFQ quantification (CPU), open-source (Apache-2.0).

Sage (https://github.com/lazear/sage) is a fast Rust DDA search engine, an open
replacement for the license-encumbered MSFragger/FragPipe. It is CPU-only and builds for
x86_64 and arm64, so it runs on cheap CPU workers (and GH200 if needed) without a GPU.

The runner prefers a Sage Docker image (``METHYL_SAGE_IMAGE``, run with no ``--gpus``) and
falls back to a native ``sage`` binary (``METHYL_SAGE_BIN`` / PATH). It writes a Sage config
JSON from the resolved ``proteomics_quant`` action config + site ``proteomics_reference``
protein FASTA, runs Sage over ``*.mzML`` under the sample dir, and produces
``{sample}.sage/results.sage.tsv`` + ``lfq.tsv`` that ``register_abundance`` normalizes to
the shared abundance contract.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

MZML_SUFFIXES: Sequence[str] = (".mzml", ".mzML", ".mgf")


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker not found on PATH; required for METHYL_SAGE_IMAGE")
    return docker


def sage_available() -> bool:
    """True when Sage can run: a Docker image env or a native binary is present."""
    if os.environ.get("METHYL_SAGE_IMAGE", "").strip() and shutil.which("docker"):
        return True
    binary = os.environ.get("METHYL_SAGE_BIN", "").strip() or "sage"
    return shutil.which(binary) is not None


def _collect_mzml(sample_dir: Path) -> List[Path]:
    found: List[Path] = []
    for suffix in MZML_SUFFIXES:
        found.extend(sorted(sample_dir.glob(f"*{suffix}")))
    return sorted(set(found))


def _resolve_reference_and_cfg(
    input_json: Mapping[str, Any], site_path: str | Path | None
) -> tuple[Dict[str, str], Dict[str, Any]]:
    from methyl_utils.action_config_resolver import (
        load_site_manifest,
        resolve_from_task_input,
        resolve_proteomics_reference,
    )

    cfg = dict(resolve_from_task_input("proteomics_quant", dict(input_json)))
    site = input_json.get("siteConfig")
    if isinstance(site, dict):
        ref = resolve_proteomics_reference(site)
    elif site_path:
        ref = resolve_proteomics_reference(load_site_manifest(site_path))
    else:
        ref = resolve_proteomics_reference()
    if cfg.get("protein_fasta"):
        ref["protein_fasta"] = str(cfg["protein_fasta"])
    return ref, cfg


def build_sage_config(
    *,
    fasta_container_path: str,
    output_dir_container: str,
    mzml_container_paths: Sequence[str],
    cfg: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a Sage config JSON from resolved proteomics_quant knobs (operator-set)."""
    prec = int(cfg.get("precursor_tol_ppm", 20))
    frag = int(cfg.get("fragment_tol_ppm", 10))
    enzyme: Dict[str, Any] = {
        "missed_cleavages": int(cfg.get("missed_cleavages", 2)),
        "min_len": int(cfg.get("min_peptide_len", 7)),
        "max_len": int(cfg.get("max_peptide_len", 50)),
        "cleave_at": str(cfg.get("cleave_at", "KR")),
        "restrict": cfg.get("restrict", "P"),
    }
    database: Dict[str, Any] = {
        "fasta": fasta_container_path,
        "enzyme": enzyme,
        "generate_decoys": bool(cfg.get("generate_decoys", True)),
    }
    if cfg.get("static_mods"):
        database["static_mods"] = dict(cfg["static_mods"])
    if cfg.get("variable_mods"):
        # e.g. {"S": [79.96633], "T": [79.96633], "Y": [79.96633]} for phospho discovery
        database["variable_mods"] = dict(cfg["variable_mods"])
    return {
        "database": database,
        "precursor_tol": {"ppm": [-prec, prec]},
        "fragment_tol": {"ppm": [-frag, frag]},
        "quant": {"lfq": bool(cfg.get("lfq", True))},
        "output_directory": output_dir_container,
        "mzml_paths": list(mzml_container_paths),
    }


def _report_path(sample_dir: Path, sample_id: str) -> Path:
    return sample_dir / f"{sample_id}.sage" / "results.sage.tsv"


def _lfq_path(sample_dir: Path, sample_id: str) -> Path:
    return sample_dir / f"{sample_id}.sage" / "lfq.tsv"


def outputs_complete(sample_dir: Path, sample_id: str) -> bool:
    return _report_path(sample_dir, sample_id).is_file()


def run_sage(
    *,
    sample_id: str,
    sample_dir: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
    site_path: str | Path | None = None,
) -> Dict[str, Optional[str]]:
    """Run a Sage DDA search + LFQ quant for one sample (CPU)."""
    payload = dict(input_json or {})
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    if outputs_complete(sample_path, sample_id):
        logger.info("Skipping Sage; results already present for %s", sample_id)
        return _result_payload(sample_path, sample_id)

    mzml = _collect_mzml(sample_path)
    if not mzml:
        raise RuntimeError(f"no mzML/mgf files under {sample_path} for Sage")

    ref, cfg = _resolve_reference_and_cfg(payload, site_path)
    fasta = ref.get("protein_fasta")
    if not fasta:
        raise RuntimeError("Sage requires site proteomics_reference.protein_fasta")
    fasta_path = Path(fasta).expanduser().resolve()
    if not fasta_path.is_file():
        raise RuntimeError(f"proteomics_reference.protein_fasta not found: {fasta_path}")

    out_dir = sample_path / f"{sample_id}.sage"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = sample_path / f"{sample_id}.sage.log"

    image = os.environ.get("METHYL_SAGE_IMAGE", "").strip()
    if image:
        # Containerized: mount sample dir + fasta dir; paths are container-relative.
        sage_cfg = build_sage_config(
            fasta_container_path=f"/fasta/{fasta_path.name}",
            output_dir_container="/workdir/" + out_dir.name,
            mzml_container_paths=[f"/workdir/{m.name}" for m in mzml],
            cfg=cfg,
        )
        config_path = out_dir / "sage_config.json"
        config_path.write_text(json.dumps(sage_cfg, indent=2), encoding="utf-8")
        uid, gid = os.getuid(), os.getgid()
        extra = shlex.split(os.environ.get("METHYL_SAGE_EXTRA_DOCKER_ARGS", "").strip() or "")
        cmd: List[str] = [
            _docker_bin(),
            "run",
            "--rm",
            "--user",
            f"{uid}:{gid}",
            "-v",
            f"{sample_path}:/workdir",
            "-v",
            f"{fasta_path.parent}:/fasta:ro",
            "-w",
            "/workdir",
            *extra,
            image,
            "sage",
            f"/workdir/{out_dir.name}/sage_config.json",
        ]
    else:
        # Native binary.
        binary = os.environ.get("METHYL_SAGE_BIN", "").strip() or "sage"
        if shutil.which(binary) is None:
            raise RuntimeError(
                "Sage not available: set METHYL_SAGE_IMAGE or install the `sage` binary "
                "(METHYL_SAGE_BIN / PATH)"
            )
        sage_cfg = build_sage_config(
            fasta_container_path=str(fasta_path),
            output_dir_container=str(out_dir),
            mzml_container_paths=[str(m) for m in mzml],
            cfg=cfg,
        )
        config_path = out_dir / "sage_config.json"
        config_path.write_text(json.dumps(sage_cfg, indent=2), encoding="utf-8")
        cmd = [binary, str(config_path)]

    log_path.write_text("COMMAND: " + " ".join(shlex.quote(p) for p in cmd) + "\n", encoding="utf-8")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(proc.stdout or "")
        handle.write(proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Sage failed")

    if not _report_path(sample_path, sample_id).is_file():
        raise RuntimeError(f"Sage did not produce results: {_report_path(sample_path, sample_id)}")
    return _result_payload(sample_path, sample_id)


def _result_payload(sample_dir: Path, sample_id: str) -> Dict[str, Optional[str]]:
    report = _report_path(sample_dir, sample_id)
    lfq = _lfq_path(sample_dir, sample_id)
    return {
        "sampleId": sample_id,
        "reportTsv": str(report) if report.is_file() else None,
        "lfqTsv": str(lfq) if lfq.is_file() else None,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Sage DDA search + LFQ (CPU)")
    parser.add_argument("sample_id")
    parser.add_argument("--sample-dir")
    parser.add_argument("--site-config")
    args = parser.parse_args(list(argv) if argv is not None else None)
    sample_dir = Path(args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}")
    result = run_sage(sample_id=args.sample_id, sample_dir=sample_dir, site_path=args.site_config)
    for key, value in result.items():
        if value is not None:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
