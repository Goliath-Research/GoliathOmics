"""Optional per-Align FASTQ staging onto worker-local scratch.

``stageFastqs: "local"`` on an Align task copies the resolved FASTQ pair
(trimmed when present) from ``/work`` into ``{local BAM dir}/fastq/`` and the
aligner reads it there. The copies live inside the BAM directory, so the
existing ``delete_bam`` step and the failure cleanup (``remove_local_align``)
free them with the BAM. The ``/work`` FASTQs are never touched.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

STAGE_FASTQS_LOCAL = "local"
LOCAL_FASTQ_SUBDIR = "fastq"


def stage_fastqs_mode(input_json: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return ``"local"`` when the task asks for local staging, else ``None``."""
    raw = (input_json or {}).get("stageFastqs")
    if raw is None:
        return None
    mode = str(raw).strip().lower()
    if mode in {"", "none", "off", "false"}:
        return None
    if mode != STAGE_FASTQS_LOCAL:
        raise ValueError(f"stageFastqs must be 'local' when set; got {raw!r}")
    return mode


def local_fastq_dir(sample_id: str, sample_dir: Path | str) -> Path:
    from methyl_utils.local_align import local_align_dir

    return local_align_dir(sample_id, sample_dir) / LOCAL_FASTQ_SUBDIR


def stage_fastqs(
    fastqs: Sequence[Path],
    *,
    sample_id: str,
    sample_dir: Path | str,
    input_json: Optional[Mapping[str, Any]],
) -> List[Path]:
    """Copy *fastqs* to worker-local scratch when requested; else return them.

    Call after the Align runner has (re)created its local BAM directory, which
    would otherwise remove the staged copies. Reads from ``/work`` with
    ``copyfile`` only (no ``copystat``/``utime``/``chmod``), so it is
    geesefs-safe; each copy lands as ``.part`` then one local rename.
    """
    if stage_fastqs_mode(input_json) is None:
        return [Path(p) for p in fastqs]
    dest_dir = local_fastq_dir(sample_id, sample_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged: List[Path] = []
    for src in fastqs:
        src_p = Path(src)
        dest = dest_dir / src_p.name
        if dest in staged:
            raise RuntimeError(f"stageFastqs: duplicate FASTQ name {src_p.name}")
        part = dest.with_name(dest.name + ".part")
        part.unlink(missing_ok=True)
        try:
            shutil.copyfile(src_p.resolve(), part)
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        staged.append(dest)
    logger.info("Staged %d FASTQ(s) for %s into %s", len(staged), sample_id, dest_dir)
    return staged
