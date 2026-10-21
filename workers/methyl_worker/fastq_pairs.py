"""Paired FASTQ discovery shared by Align and fastp trim.

This module is aligner-agnostic. Clara ``trim_fastq`` imports from here so
FASTQ pairing does not load an aligner runner.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from methyl_utils.sample_arm_layout import sample_root_from_sample_dir

logger = logging.getLogger(__name__)

FASTQ_SUFFIXES: Sequence[str] = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
# {prefix}_1 / {prefix}_2 and Illumina {prefix}_R1[_001] / {prefix}_R2[_001]
# Capture separator and optional lane segment so sample_R1 ≠ sample_1 and
# _001 ≠ _002 (they are distinct pairs, not collisions on the same mate slot).
_FASTQ_MATE_RE = re.compile(
    r"^(?P<prefix>.+)(?P<sep>_R|_r|_)(?P<mate>[12])(?:_(?P<segment>[0-9]{3}))?$"
)
_FASTQ_BARE_R_RE = re.compile(r"^R(?P<mate>[12])(?:_(?P<segment>[0-9]{3}))?$", re.IGNORECASE)
_SKIP_FASTQ_DIR_NAMES = frozenset({"tmp", ".caas", ".unused_merged"})
_ALIGNER_MODE_LEAVES = frozenset({"linear", "pangenome", "pangenome_wgbs"})
_ALIGNER_ARM_PREFIXES = ("align.", "extract.")


def _is_aligner_leaf_dirname(name: str) -> bool:
    """Skip FASTQ copies under aligner/mode leaves when sampleDir is the sample root."""
    try:
        from methyl_domain.sample_content_store import is_sample_arm_dirname

        return is_sample_arm_dirname(name)
    except ImportError:
        if not name or name in {".", ".."}:
            return False
        if name.startswith(_ALIGNER_ARM_PREFIXES):
            return True
        return name in _ALIGNER_MODE_LEAVES


def _matches_fastq(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in FASTQ_SUFFIXES)


def _fastq_stem(path: Path) -> str:
    name = path.name
    lower = name.lower()
    for suffix in FASTQ_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _mate_group(path: Path) -> Tuple[Tuple[str, str, str, str], str] | None:
    """Return ((parent, prefix, sep, segment), mate) or None if unparseable.

    ``sep`` and ``segment`` are part of the pair identity so ``sample_R1`` does
    not collide with ``sample_1``, and ``_R1_001`` does not collide with ``_R1_002``.
    """
    stem = _fastq_stem(path)
    match = _FASTQ_MATE_RE.match(stem)
    if match is None:
        match = _FASTQ_BARE_R_RE.match(stem)
        if match is None:
            return None
        prefix = "R"
        sep = "R"
    else:
        prefix = match.group("prefix")
        sep = match.group("sep")
    segment = match.group("segment") or ""
    parent = str(path.parent.resolve())
    return (parent, prefix, sep, segment), match.group("mate")


def _inode_key(path: Path) -> Tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino)


def _symlink_usable_under_workdir(path: Path, sample_dir: Path) -> bool:
    """True when Clara can open ``path`` after ``sample_dir`` is mounted at ``/workdir``.

    Clara follows the *stored* symlink target, not the host ``resolve()``. An
    absolute link whose dest is still under *sample_dir* is readable here and
    can win inode-dedup, but is unreadable in the container.
    """
    if not path.is_symlink():
        return True
    try:
        stored = path.readlink()
    except OSError:
        return False
    if stored.is_absolute():
        return False
    dest = (path.parent / stored).resolve()
    try:
        dest.relative_to(sample_dir.resolve())
    except ValueError:
        return False
    return dest.is_file()


def _collect_fastqs(sample_dir: Path) -> List[Path]:
    candidates: List[Path] = []
    for path in sample_dir.rglob("*"):
        if not path.is_file() or not _matches_fastq(path):
            continue
        rel_parts = path.relative_to(sample_dir).parts[:-1]
        rel_dirs = {part.lower() for part in rel_parts}
        if rel_dirs & _SKIP_FASTQ_DIR_NAMES:
            continue
        if any(_is_aligner_leaf_dirname(part) for part in rel_parts):
            continue
        if not _symlink_usable_under_workdir(path, sample_dir):
            logger.warning(
                "Skipping FASTQ symlink %s; stored target is not a relative "
                "path under %s (Clara /workdir cannot follow it)",
                path.relative_to(sample_dir),
                sample_dir,
            )
            continue
        candidates.append(path)
    found: List[Path] = []
    seen_paths: set[Path] = set()
    seen_inodes: set[Tuple[int, int]] = set()
    # Regular files before symlinks so a leftover link cannot win inode-dedup.
    for path in sorted(candidates, key=lambda p: (p.is_symlink(), str(p))):
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        inode = _inode_key(path)
        if inode is not None and inode in seen_inodes:
            continue
        seen_paths.add(resolved)
        if inode is not None:
            seen_inodes.add(inode)
        found.append(path)
    return found


def canonical_trimmed_fastqs(sample_dir: Path, sample_id: str) -> Tuple[Path, Path]:
    """Remediation outputs that Align prefers: ``{id}_1.trimmed.fastq.gz`` / ``_2``."""
    root = Path(sample_dir)
    return (
        root / f"{sample_id}_1.trimmed.fastq.gz",
        root / f"{sample_id}_2.trimmed.fastq.gz",
    )


def _is_trimmed_fastq_name(path: Path) -> bool:
    return ".trimmed." in path.name.lower()


def _is_concat_merge_fastq_name(path: Path) -> bool:
    """Vendor ``{id}_merged_1.fastq.gz`` concatenations — often desynced across lanes."""
    name = path.name.lower()
    return "_merged_" in name or name.startswith("merged_")


def fastq_home_dir(sample_dir: Path) -> Path:
    """Sample root that holds downloaded FASTQs.

    Align ``sampleDir`` is an ``align.*`` leaf. Downloads stay on the sample
    root; align stages those files to local scratch and does not copy them
    into the leaf.
    """
    return Path(sample_root_from_sample_dir(sample_dir))


def _unique_dirs(*paths: Path) -> List[Path]:
    out: List[Path] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_dir():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _restore_download_fastqs(sample_dir: Path, sample_id: str) -> None:
    """Bring CAAS-harvested FASTQs back under the sample root (Align skips ``.caas``)."""
    try:
        from methyl_domain.sample_content_store import restore_sample_fastq_products

        restore_sample_fastq_products(sample_dir, sample_id)
    except Exception:
        logger.debug("CAAS FASTQ restore skipped for %s", sample_id, exc_info=True)


def _pair_fastqs(considered: List[Path], sample_dir: Path) -> List[Path]:
    groups: Dict[Tuple[str, str, str, str], Dict[str, Path]] = {}
    leftovers: List[Path] = []
    for path in considered:
        parsed = _mate_group(path)
        if parsed is None:
            leftovers.append(path)
            continue
        key, mate = parsed
        mates = groups.setdefault(key, {})
        if mate in mates:
            existing = mates[mate]
            raise RuntimeError(
                f"Duplicate FASTQ mate {mate} under {sample_dir}: "
                f"{existing.relative_to(sample_dir)} and {path.relative_to(sample_dir)}"
            )
        mates[mate] = path

    pairs: List[Tuple[Path, Path]] = []
    for key in sorted(groups):
        mates = groups[key]
        if "1" in mates and "2" in mates:
            pairs.append((mates["1"], mates["2"]))
        else:
            leftovers.extend(mates.values())

    if not pairs:
        incomplete_named = [path for path in leftovers if _mate_group(path) is not None]
        unparsed = [path for path in leftovers if _mate_group(path) is None]
        if incomplete_named:
            raise RuntimeError(
                f"Incomplete FASTQ mate group(s) under {sample_dir}: "
                + ", ".join(p.name for p in incomplete_named)
            )
        if len(unparsed) >= 2 and len(unparsed) % 2 == 0:
            logger.info(
                "Using %s unparsed FASTQs under %s as sequential Clara --in-fq pairs",
                len(unparsed),
                sample_dir,
            )
            return list(unparsed)
        raise RuntimeError(
            f"Expected paired FASTQ files under {sample_dir}, found {len(considered)}"
            + (f": {', '.join(p.name for p in considered)}" if considered else "")
        )
    lane_pairs = [
        pair for pair in pairs if not any(_is_concat_merge_fastq_name(path) for path in pair)
    ]
    if lane_pairs and len(lane_pairs) < len(pairs):
        logger.info(
            "Ignoring %s concatenated *_merged_* pair(s) under %s; using %s lane pair(s)",
            len(pairs) - len(lane_pairs),
            sample_dir,
            len(lane_pairs),
        )
        pairs = lane_pairs
    if leftovers:
        logger.warning(
            "Ignoring unpaired FASTQ(s) under %s: %s",
            sample_dir,
            ", ".join(str(p.relative_to(sample_dir)) for p in leftovers),
        )
    ordered: List[Path] = []
    for r1, r2 in pairs:
        ordered.extend((r1, r2))
    return ordered


def _resolve_in_dir(
    sample_dir: Path,
    sample_id: str,
    *,
    prefer_trimmed: bool,
) -> List[Path]:
    trimmed = list(canonical_trimmed_fastqs(sample_dir, sample_id))
    skip = {p.resolve() for p in trimmed}
    considered: List[Path] = []
    for path in _collect_fastqs(sample_dir):
        if (not prefer_trimmed) and (path.resolve() in skip or _is_trimmed_fastq_name(path)):
            continue
        considered.append(path)
    return _pair_fastqs(considered, sample_dir)


def _demux_pair(sample_dir: Path) -> List[Path] | None:
    """epi-GBS products written into the align leaf (``*_demux_*``)."""
    if not sample_dir.is_dir():
        return None
    files = [
        path
        for path in sample_dir.iterdir()
        if path.is_file() and _matches_fastq(path) and "_demux_" in path.name.lower()
    ]
    if len(files) < 2:
        return None
    try:
        return _pair_fastqs(files, sample_dir)
    except RuntimeError:
        logger.debug("demux FASTQs under %s are not a complete pair", sample_dir, exc_info=True)
        return None


def resolve_paired_fastqs(
    sample_dir: Path,
    sample_id: str,
    *,
    prefer_trimmed: bool = True,
    prefer_demux: bool = True,
) -> List[Path]:
    """Return paired-end FASTQs (one or more pairs) for a sample.

    Clara ``fq2bam_meth --in-fq`` accepts sequential R1/R2 pairs. Named
    ``_1``/``_2`` (and Illumina ``_R1``/``_R2``) grouping is preferred when it
    yields complete pairs. Sequential fallback applies only to files with no
    mate identity — incomplete named groups fail closed so two R1s (or an R1
    plus an unrelated leftover) cannot be aligned as mates.

    Downloaded reads live on the sample root. When ``sample_dir`` is an align
    leaf, those root files are the source and copies inside the leaf are
    ignored. ``*_demux_*`` products in the leaf are what Align consumes for
    epi-GBS, including after trim. Otherwise trimmed
    ``{id}_1.trimmed.fastq.gz`` / ``_2`` wins, checked on the sample root
    first. Trim must pass ``prefer_trimmed=False`` (and ``prefer_demux=False``)
    so it never feeds those outputs back into fastp. An align leaf that is the
    only directory with reads (no sample-root pair) is still accepted.
    """
    sample_dir = Path(sample_dir)
    home = fastq_home_dir(sample_dir)
    _restore_download_fastqs(home, sample_id)

    # Demux products are the reads Align should use. They are written after
    # trim, so they win over trimmed sources that still sit on the sample root.
    if prefer_demux and home.resolve() != sample_dir.resolve():
        demux = _demux_pair(sample_dir)
        if demux:
            return demux

    if prefer_trimmed:
        for base in _unique_dirs(home, sample_dir):
            trimmed = list(canonical_trimmed_fastqs(base, sample_id))
            if all(path.is_file() for path in trimmed):
                return trimmed

    home_files = _collect_fastqs(home) if home.is_dir() else []
    if home_files or home.resolve() == sample_dir.resolve():
        return _resolve_in_dir(home, sample_id, prefer_trimmed=prefer_trimmed)
    return _resolve_in_dir(sample_dir, sample_id, prefer_trimmed=prefer_trimmed)
