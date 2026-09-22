"""Generic barcode demultiplex for epi-GBS (config-driven; no crop-specific Python)."""

from __future__ import annotations

import csv
import gzip
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

FASTQ_SUFFIXES: Sequence[str] = (".fastq.gz", ".fq.gz", ".fastq", ".fq")


def _open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def _open_write(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "wt", encoding="utf-8")
    return path.open("wt", encoding="utf-8")


def _find_pair(sample_dir: Path) -> Tuple[Path, Path]:
    files = sorted(
        p for p in sample_dir.iterdir() if p.is_file() and p.name.endswith(FASTQ_SUFFIXES)
    )
    r1 = next((p for p in files if "_R1" in p.name or ".R1." in p.name or p.name.endswith("_1.fastq.gz")), None)
    r2 = next((p for p in files if "_R2" in p.name or ".R2." in p.name or p.name.endswith("_2.fastq.gz")), None)
    if r1 is None or r2 is None:
        if len(files) >= 2:
            return files[0], files[1]
        raise RuntimeError(f"Need paired FASTQs in {sample_dir}")
    return r1, r2


def _load_barcode(barcode_tsv: Path, sample_id: str) -> str:
    with barcode_tsv.open("rt", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t" if barcode_tsv.suffix.lower() in {".tsv", ".txt"} else ",")
        if not reader.fieldnames:
            raise RuntimeError(f"Empty barcode table: {barcode_tsv}")
        fields = {str(f).strip().lower(): f for f in reader.fieldnames}
        sid_col = fields.get("sample_id") or fields.get("sample") or fields.get("sampleid")
        bc_col = fields.get("barcode") or fields.get("index") or fields.get("barcode_seq")
        if not sid_col or not bc_col:
            raise RuntimeError(
                f"barcode TSV needs sample_id + barcode columns; got {reader.fieldnames}"
            )
        for row in reader:
            if str(row.get(sid_col) or "").strip() == sample_id:
                bc = str(row.get(bc_col) or "").strip().upper()
                if not bc:
                    raise RuntimeError(f"Empty barcode for sample_id={sample_id}")
                return bc
    raise RuntimeError(f"sample_id={sample_id} not found in {barcode_tsv}")


def run_demultiplex(
    *,
    sample_id: str,
    sample_dir: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Demultiplex PE FASTQs by leading barcode for one sample_id.

    Config (resolvedConfig / demultiplex actionConfig):
      - barcode_tsv / barcodeTsv (required unless skip)
      - barcode_len (optional; default len(barcode))
      - output_r1 / output_r2 filenames (optional)
    """
    payload = dict(input_json or {})
    resolved = dict(payload.get("resolvedConfig") or {})
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    if bool(resolved.get("skip") or payload.get("skipDemultiplex")):
        r1, r2 = _find_pair(sample_path)
        logger.info("demultiplex skipped for %s; using %s / %s", sample_id, r1.name, r2.name)
        return {
            "sampleId": sample_id,
            "fastqR1": str(r1),
            "fastqR2": str(r2),
            "n_reads_kept": None,
            "skipped": True,
        }

    barcode_tsv = resolved.get("barcode_tsv") or resolved.get("barcodeTsv") or payload.get("barcodeTsv")
    if not barcode_tsv:
        raise RuntimeError(
            "sample.demultiplex requires resolvedConfig.barcode_tsv "
            "(or set skip=true / skipDemultiplex when demux is upstream)."
        )
    bc_path = Path(str(barcode_tsv)).expanduser()
    if not bc_path.is_file():
        raise RuntimeError(f"barcode_tsv not found: {bc_path}")

    barcode = _load_barcode(bc_path, sample_id)
    barcode_len = int(resolved.get("barcode_len") or resolved.get("barcodeLen") or len(barcode))
    r1_in, r2_in = _find_pair(sample_path)
    out_r1 = sample_path / str(resolved.get("output_r1") or f"{sample_id}_demux_R1.fastq.gz")
    out_r2 = sample_path / str(resolved.get("output_r2") or f"{sample_id}_demux_R2.fastq.gz")

    kept = 0
    with _open_text(r1_in) as f1, _open_text(r2_in) as f2, _open_write(out_r1) as o1, _open_write(out_r2) as o2:
        while True:
            h1 = f1.readline()
            if not h1:
                break
            s1 = f1.readline()
            p1 = f1.readline()
            q1 = f1.readline()
            h2 = f2.readline()
            s2 = f2.readline()
            p2 = f2.readline()
            q2 = f2.readline()
            if not q2:
                break
            if s1[:barcode_len].upper() != barcode[:barcode_len]:
                continue
            # Strip barcode from R1 sequence/qual
            s1_out = s1[barcode_len:]
            q1_out = q1[barcode_len:]
            o1.write(h1)
            o1.write(s1_out if s1_out.endswith("\n") else s1_out + "\n")
            o1.write(p1 if p1.startswith("+") else "+\n")
            o1.write(q1_out if q1_out.endswith("\n") else q1_out + "\n")
            o2.write(h2)
            o2.write(s2)
            o2.write(p2 if p2.startswith("+") else "+\n")
            o2.write(q2)
            kept += 1

    logger.info("demultiplex %s: kept %d read pairs (barcode=%s)", sample_id, kept, barcode)
    return {
        "sampleId": sample_id,
        "fastqR1": str(out_r1),
        "fastqR2": str(out_r2),
        "n_reads_kept": kept,
        "barcode": barcode,
        "skipped": False,
    }
