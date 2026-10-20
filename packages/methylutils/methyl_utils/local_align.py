"""Worker-local alignment products (BAM / .bai) for SamplePrep.

The aligned BAM never lives on the shared ``/work`` mount. Align writes it
under the worker's local scratch root and every BAM consumer (alignment QC,
fragmentomics, MethylExtractor, ``sample.delete_bam``) runs on the same worker
because those actions are catalog-pinned with ``require_previous_worker``.

Scratch root: ``METHYL_LOCAL_TMP``, else ``$TMPDIR/methyl-pb``, else
``/tmp/methyl-pb``. Per-sample products go to ``<root>/bam/<slug>-<digest>/``
where ``digest`` hashes the resolved ``sampleDir`` so separate arms of one
sample do not collide.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Optional

DEFAULT_LOCAL_TMP_ROOT = Path("/tmp/methyl-pb")


def local_tmp_root() -> Path:
    """Worker-local scratch root (never the shared work mount)."""
    explicit = os.environ.get("METHYL_LOCAL_TMP", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    tmpdir = os.environ.get("TMPDIR", "").strip()
    if tmpdir:
        return Path(tmpdir).expanduser() / "methyl-pb"
    return DEFAULT_LOCAL_TMP_ROOT


def sample_scratch_name(sample_id: str, sample_dir: Path | str) -> str:
    """Stable ``<slug>-<digest>`` name for one sample arm."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", sample_id).strip("._") or "sample"
    digest = hashlib.sha256(str(Path(sample_dir).resolve()).encode()).hexdigest()[:10]
    return f"{slug}-{digest}"


def local_align_dir(sample_id: str, sample_dir: Path | str) -> Path:
    """Directory that holds this arm's BAM and index on the worker."""
    return local_tmp_root() / "bam" / sample_scratch_name(sample_id, sample_dir)


def local_bam_path(sample_id: str, sample_dir: Path | str) -> Path:
    return local_align_dir(sample_id, sample_dir) / f"{sample_id}.bam"


def find_local_bam(sample_id: str, sample_dir: Path | str) -> Optional[Path]:
    """Return the worker-local BAM when present and non-empty."""
    path = local_bam_path(sample_id, sample_dir)
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def remove_local_align(sample_id: str, sample_dir: Path | str) -> int:
    """Delete the worker-local BAM, index and align directory. Returns files removed."""
    root = local_align_dir(sample_id, sample_dir)
    if not root.exists():
        return 0
    removed = sum(1 for p in root.rglob("*") if p.is_file())
    shutil.rmtree(root, ignore_errors=True)
    return removed
