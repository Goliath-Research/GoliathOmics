#!/usr/bin/env python3
"""
Split combined per-chromosome sample HDF5 files into MethylPipeline layout.

Upstream extraction may write ``{chrom}.h5`` with all methylation contexts in one
file. MethylCentroid expects ``{chrom}-CG.h5``, ``{chrom}-CHG.h5``, ``{chrom}-CHH.h5``.

This script scans ``/work/samples/<sample_id>/``, loads each ``{chrom}.h5``, exports
per-context files, then renames the source to ``{chrom}.h5.bak``.

Usage:
  source .venv/bin/activate
  python scripts/split_sample_h5_by_context.py --samples-base /work/samples
  python scripts/check_sample_h5_coverage.py
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import pwd
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from methyl_utils import MethylSample
from methyl_utils.core.methyl_frame import MethylCentroid

logger = logging.getLogger(__name__)

AUTOSOMES: List[str] = [str(i) for i in range(1, 23)]
SEX_CHROMOSOMES: List[str] = ["X", "Y"]
DEFAULT_CHROMOSOMES: List[str] = [*AUTOSOMES, *SEX_CHROMOSOMES]
DEFAULT_CONTEXTS: List[str] = ["CG", "CHG", "CHH"]
DEFAULT_SAMPLES_BASE = "/work/samples"
DEFAULT_OWNER = "ubuntu"

SOURCE_H5_RE = re.compile(r"^(?:[1-9]|1[0-9]|2[0-2]|X|Y)\.h5$")
PIPELINE_H5_RE = re.compile(r"^(?:[1-9]|1[0-9]|2[0-2]|X|Y)-(CG|CHG|CHH)\.h5$")

def _filter_context(sample: MethylSample, context: str) -> MethylSample:
    """Subset rows by methylation context (CG / CHG / CHH)."""
    return sample[sample.context == context]


CONTEXT_FILTERS: Dict[str, Callable[[MethylSample], MethylSample]] = {
    "CG": lambda s: _filter_context(s, "CG"),
    "CHG": lambda s: _filter_context(s, "CHG"),
    "CHH": lambda s: _filter_context(s, "CHH"),
}


@dataclass
class SplitResult:
    sample_id: str
    chrom: str
    source_path: Path
    status: str  # ok | skipped | failed | dry_run
    message: str = ""
    rows_by_context: Dict[str, int] = field(default_factory=dict)
    outputs: List[str] = field(default_factory=list)


def parse_chromosome_list(raw: Optional[str]) -> List[str]:
    if not raw or not raw.strip():
        return list(DEFAULT_CHROMOSOMES)
    return [p.strip() for p in raw.split(",") if p.strip()]


def parse_context_list(raw: Optional[str]) -> List[str]:
    if not raw or not raw.strip():
        return list(DEFAULT_CONTEXTS)
    out = [p.strip().upper() for p in raw.split(",") if p.strip()]
    bad = [c for c in out if c not in CONTEXT_FILTERS]
    if bad:
        raise ValueError(f"Unknown contexts: {bad}. Allowed: {sorted(CONTEXT_FILTERS)}")
    return out


def _owner_uid_gid(username: str) -> Tuple[int, int]:
    pw = pwd.getpwnam(username)
    return pw.pw_uid, pw.pw_gid


def _can_chown(path: Path) -> bool:
    if not path.exists():
        return False
    if os.geteuid() == 0:
        return True
    try:
        return path.stat().st_uid == os.geteuid()
    except OSError:
        return False


def fix_path_permissions(path: Path, *, uid: int, gid: int) -> None:
    """Best-effort chmod/chown so ubuntu can read written H5 files."""
    if not path.exists():
        return
    try:
        if _can_chown(path):
            os.chown(path, uid, gid)
        mode = path.stat().st_mode
        if path.is_dir():
            os.chmod(path, (mode | 0o755) & 0o7777)
        else:
            os.chmod(path, (mode | 0o644) & 0o7777)
    except OSError as exc:
        logger.warning("Could not fix permissions on %s: %s", path, exc)


def discover_source_files(
    sample_dir: Path,
    chromosomes: Set[str],
) -> List[Tuple[str, Path]]:
    """Return (chrom, path) for combined {chrom}.h5 sources to split."""
    found: List[Tuple[str, Path]] = []
    for entry in sorted(sample_dir.iterdir()):
        if not entry.is_file() or entry.suffix.lower() != ".h5":
            continue
        name = entry.name
        if PIPELINE_H5_RE.match(name):
            continue
        m = SOURCE_H5_RE.match(name)
        if not m:
            continue
        chrom = name[:-3]  # strip .h5
        if chromosomes and chrom not in chromosomes:
            continue
        found.append((chrom, entry))
    return found


def _targets_exist(sample_dir: Path, chrom: str, contexts: Sequence[str]) -> bool:
    return all((sample_dir / f"{chrom}-{ctx}.h5").is_file() for ctx in contexts)


def _is_centroid_h5(path: Path) -> bool:
    import h5py

    try:
        with h5py.File(path, "r") as f:
            if "methylation_data" not in f:
                return False
            grp = f["methylation_data"]
            if not isinstance(grp, h5py.Group):
                return False
            keys = set(grp.keys())
            centroid_markers = {"Sm", "Su", "Sc2", "Swx2"}
            return centroid_markers.issubset(keys)
    except OSError:
        return False


def split_one_chromosome(
    sample_dir: Path,
    chrom: str,
    source_path: Path,
    *,
    contexts: Sequence[str],
    overwrite: bool,
    dry_run: bool,
    skip_empty: bool,
    archive_source: bool,
    owner_uid: int,
    owner_gid: int,
) -> SplitResult:
    sample_id = sample_dir.name
    result = SplitResult(
        sample_id=sample_id,
        chrom=chrom,
        source_path=source_path,
        status="ok",
    )

    if _is_centroid_h5(source_path):
        result.status = "skipped"
        result.message = "centroid HDF5 (not a per-sample extract)"
        return result

    if not overwrite and _targets_exist(sample_dir, chrom, contexts):
        result.status = "skipped"
        result.message = "all target context files already exist"
        return result

    if dry_run:
        result.status = "dry_run"
        for ctx in contexts:
            result.outputs.append(str(sample_dir / f"{chrom}-{ctx}.h5"))
        result.message = f"would split {source_path.name} -> {', '.join(contexts)}"
        if archive_source:
            result.message += f"; archive {source_path.name} -> {source_path.name}.bak"
        return result

    try:
        loaded = MethylSample.load_from_h5(source_path)
    except Exception as exc:
        result.status = "failed"
        result.message = f"load failed: {exc}"
        return result

    if isinstance(loaded, MethylCentroid):
        result.status = "skipped"
        result.message = "loaded object is MethylCentroid"
        return result

    exported: List[str] = []
    for ctx in contexts:
        filt = CONTEXT_FILTERS[ctx]
        try:
            part = filt(loaded)  # type: ignore[arg-type]
        except Exception as exc:
            result.status = "failed"
            result.message = f"filter {ctx} failed: {exc}"
            return result

        n_rows = len(part)
        result.rows_by_context[ctx] = n_rows
        out_path = sample_dir / f"{chrom}-{ctx}.h5"

        if n_rows == 0:
            if skip_empty:
                logger.warning(
                    "%s/%s: %s has 0 rows; skipping empty export",
                    sample_id,
                    chrom,
                    ctx,
                )
                continue
            result.status = "failed"
            result.message = f"context {ctx} has 0 rows"
            return result

        if out_path.is_file() and not overwrite:
            exported.append(ctx)
            continue

        try:
            part.context_metadata = ctx
            part.save_to_h5(out_path, compressed=True)
            fix_path_permissions(out_path, uid=owner_uid, gid=owner_gid)
            exported.append(ctx)
            result.outputs.append(str(out_path))
        except Exception as exc:
            result.status = "failed"
            result.message = f"write {out_path.name} failed: {exc}"
            return result

    required_ok = set(contexts)
    if skip_empty:
        required_ok = {c for c in contexts if result.rows_by_context.get(c, 0) > 0}
        if not required_ok:
            result.status = "failed"
            result.message = "all contexts empty"
            return result

    if set(exported) != required_ok:
        result.status = "failed"
        missing_ctx = sorted(required_ok - set(exported))
        result.message = f"did not export: {', '.join(missing_ctx)}"
        return result

    archive_note = ""
    if archive_source and source_path.is_file():
        bak_path = source_path.with_suffix(".h5.bak")
        if bak_path.exists() and not overwrite:
            result.status = "failed"
            result.message = f"archive target exists: {bak_path.name}"
            return result
        try:
            source_path.rename(bak_path)
            fix_path_permissions(bak_path, uid=owner_uid, gid=owner_gid)
            archive_note = f"; archived {source_path.name} -> {bak_path.name}"
        except OSError as exc:
            result.status = "failed"
            result.message = f"archive failed: {exc}"
            return result

    counts = ", ".join(f"{k}={v:,}" for k, v in sorted(result.rows_by_context.items()))
    result.message = f"exported {', '.join(exported)} ({counts}){archive_note}"
    return result


def list_sample_dirs(samples_base: Path, only: Optional[Set[str]]) -> List[Path]:
    if not samples_base.is_dir():
        raise FileNotFoundError(f"samples base not found: {samples_base}")
    dirs = sorted(
        (p.resolve() for p in samples_base.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    if only:
        dirs = [d for d in dirs if d.name in only]
    return dirs


def write_report(path: Path, results: Sequence[SplitResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "sample_id",
                "chrom",
                "source",
                "status",
                "message",
                "rows_CG",
                "rows_CHG",
                "rows_CHH",
                "outputs",
            ]
        )
        for r in results:
            w.writerow(
                [
                    r.sample_id,
                    r.chrom,
                    str(r.source_path),
                    r.status,
                    r.message,
                    r.rows_by_context.get("CG", ""),
                    r.rows_by_context.get("CHG", ""),
                    r.rows_by_context.get("CHH", ""),
                    ";".join(r.outputs),
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Split combined {chrom}.h5 sample files into {chrom}-{context}.h5 exports.",
    )
    p.add_argument(
        "--samples-base",
        default=DEFAULT_SAMPLES_BASE,
        help=f"Root with one folder per sample (default: {DEFAULT_SAMPLES_BASE})",
    )
    p.add_argument(
        "--contexts",
        default=",".join(DEFAULT_CONTEXTS),
        help=f"Contexts to export (default: {','.join(DEFAULT_CONTEXTS)})",
    )
    p.add_argument(
        "--chromosomes",
        default=",".join(DEFAULT_CHROMOSOMES),
        help="Comma-separated chromosomes to process (default: 1-22,X,Y)",
    )
    p.add_argument("--overwrite", action="store_true", help="Replace existing exports")
    p.add_argument("--dry-run", action="store_true", help="Log actions only")
    p.add_argument(
        "--sample",
        action="append",
        default=[],
        metavar="ID",
        help="Limit to sample directory name(s) under samples-base",
    )
    p.add_argument("--report", type=Path, help="Write CSV report")
    p.add_argument(
        "--skip-empty",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip writing context file when it has 0 rows (default: true)",
    )
    p.add_argument(
        "--no-archive-source",
        action="store_true",
        help="Keep {chrom}.h5 after split (default: rename to {chrom}.h5.bak)",
    )
    p.add_argument(
        "--owner",
        default=DEFAULT_OWNER,
        help=f"Unix owner for written files (default: {DEFAULT_OWNER})",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        contexts = parse_context_list(args.contexts)
        chromosomes = set(parse_chromosome_list(args.chromosomes))
        owner_uid, owner_gid = _owner_uid_gid(args.owner)
    except (ValueError, KeyError) as exc:
        logger.error("%s", exc)
        return 2

    samples_base = Path(args.samples_base).expanduser().resolve()
    only_samples = set(args.sample) if args.sample else None
    archive_source = not args.no_archive_source

    try:
        sample_dirs = list_sample_dirs(samples_base, only_samples)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2

    if not sample_dirs:
        logger.error("No sample directories under %s", samples_base)
        return 2

    all_results: List[SplitResult] = []
    n_failed = 0

    for sample_dir in sample_dirs:
        sources = discover_source_files(sample_dir, chromosomes)
        if not sources:
            continue
        for chrom, source_path in sources:
            res = split_one_chromosome(
                sample_dir,
                chrom,
                source_path,
                contexts=contexts,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                skip_empty=args.skip_empty,
                archive_source=archive_source,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
            all_results.append(res)
            if res.status == "failed":
                n_failed += 1
                logger.error("%s/%s: %s", res.sample_id, res.chrom, res.message)
            elif res.status in ("ok", "dry_run"):
                logger.info("%s/%s: %s", res.sample_id, res.chrom, res.message)
            else:
                logger.info("%s/%s: skipped — %s", res.sample_id, res.chrom, res.message)

    if not all_results:
        logger.warning("No combined {chrom}.h5 files found under %s", samples_base)
        return 0

    ok = sum(1 for r in all_results if r.status in ("ok", "dry_run"))
    skipped = sum(1 for r in all_results if r.status == "skipped")
    print(f"Samples base: {samples_base}")
    print(f"Processed: {len(all_results)} chromosome file(s)  ok/dry-run: {ok}  skipped: {skipped}  failed: {n_failed}")

    if args.report:
        write_report(args.report, all_results)
        print(f"Wrote report: {args.report}")

    return 1 if n_failed else 0


if __name__ == "__main__":
    sys.exit(main())