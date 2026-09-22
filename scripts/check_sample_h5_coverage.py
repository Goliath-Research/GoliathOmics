#!/usr/bin/env python3
"""
Verify every sample under /work/samples/<sample_id>/ has the expected H5 extracts
and that each file is readable by user ubuntu (fix ownership/permissions when needed).

Autosomes 1–22 are always required ({chrom}-CG.h5 by default). Sex chromosomes
are inferred per sample (shared pool across diseases):

  - Y present  → require X and Y (typical male)
  - X only     → require X only (typical female); missing Y is OK
  - neither    → incomplete

If samples use combined ``{chrom}.h5`` files (all contexts), run
``scripts/split_sample_h5_by_context.py`` first to produce ``{chrom}-CG.h5``, etc.

Usage:
  python scripts/check_sample_h5_coverage.py
  python scripts/check_sample_h5_coverage.py --samples-base /work/samples --report /tmp/sample_h5.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import pwd
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional, Sequence, Tuple

AUTOSOMES: List[str] = [str(i) for i in range(1, 23)]
DEFAULT_CONTEXT = "CG"
DEFAULT_SAMPLES_BASE = "/work/samples"
DEFAULT_OWNER = "ubuntu"
SexChromMode = Literal["auto", "both", "any"]


@dataclass
class SampleCheckResult:
    sample_id: str
    sample_dir: Path
    sex_profile: str
    expected: int
    found: int
    missing: List[str] = field(default_factory=list)
    inaccessible: List[str] = field(default_factory=list)
    fixed: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return (
            len(self.missing) == 0
            and len(self.inaccessible) == 0
            and self.expected > 0
        )


def h5_name(chrom: str, context: str) -> str:
    return f"{chrom}-{context}.h5"


def _autosome_names(context: str) -> List[str]:
    return [h5_name(c, context) for c in AUTOSOMES]


def _owner_uid_gid(username: str) -> Tuple[int, int]:
    pw = pwd.getpwnam(username)
    return int(pw.pw_uid), int(pw.pw_gid)


def _can_chown(path: Path) -> bool:
    if not path.exists():
        return False
    if os.geteuid() == 0:
        return True
    try:
        return path.stat().st_uid == os.geteuid()
    except OSError:
        return False


def _ensure_dir_traversable(
    directory: Path,
    *,
    uid: int,
    gid: int,
    fix: bool,
    stop_at: Path,
) -> Tuple[bool, bool]:
    """Ensure directory is searchable; return (ok, fixed_any)."""
    fixed_any = False
    current = directory.resolve()
    stop = stop_at.resolve()
    while True:
        if not current.exists() or not current.is_dir():
            return False, fixed_any
        if os.access(current, os.X_OK):
            pass
        elif fix:
            try:
                if _can_chown(current):
                    os.chown(current, uid, gid)
                    fixed_any = True
                mode = current.stat().st_mode
                os.chmod(current, mode | 0o711)
                fixed_any = True
            except OSError:
                return False, fixed_any
        if not os.access(current, os.X_OK):
            return False, fixed_any
        if current == stop:
            break
        if current.parent == current:
            break
        current = current.parent
    return True, fixed_any


def _readable_h5(path: Path) -> bool:
    if not path.is_file():
        return False
    if not os.access(path, os.R_OK):
        return False
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


def ensure_h5_accessible(
    path: Path,
    *,
    uid: int,
    gid: int,
    fix: bool,
    stop_at: Path,
) -> Tuple[bool, bool]:
    """
    Return (readable, fixed).

    When ``fix`` is true, chown to ubuntu (if allowed) and chmod u+rw, go+r on the
    file; ensure parent directories are traversable.
    """
    if not path.is_file():
        return False, False

    if _readable_h5(path):
        return True, False

    if not fix:
        return False, False

    fixed = False
    parent_ok, parent_fixed = _ensure_dir_traversable(
        path.parent, uid=uid, gid=gid, fix=True, stop_at=stop_at
    )
    fixed = fixed or parent_fixed
    if not parent_ok:
        return _readable_h5(path), fixed

    try:
        if _can_chown(path):
            os.chown(path, uid, gid)
            fixed = True
        mode = path.stat().st_mode
        os.chmod(path, (mode | 0o644) & 0o7777)
        fixed = True
    except OSError:
        return False, fixed

    return _readable_h5(path), fixed


def _required_h5_names(
    sample_dir: Path,
    context: str,
    sex_mode: SexChromMode,
) -> Tuple[List[str], str]:
    ctx = context.strip() or DEFAULT_CONTEXT
    x_name = h5_name("X", ctx)
    y_name = h5_name("Y", ctx)
    has_x = (sample_dir / x_name).is_file()
    has_y = (sample_dir / y_name).is_file()
    auto_names = _autosome_names(ctx)

    if sex_mode == "both":
        return [*auto_names, x_name, y_name], "n/a"
    if sex_mode == "any":
        return [*auto_names, x_name, y_name], "unknown"
    if has_y:
        return [*auto_names, x_name, y_name], "male"
    if has_x:
        return [*auto_names, x_name], "female"
    return list(auto_names), "unknown"


def _compute_expected_and_found(
    sample_dir: Path,
    *,
    auto_names: List[str],
    x_name: str,
    y_name: str,
    has_x: bool,
    has_y: bool,
    profile: str,
    sex_mode: SexChromMode,
    missing: List[str],
    inaccessible: List[str],
) -> Tuple[int, int]:
    """Derive expected/found counts from final missing list and sex profile."""
    inacc = set(inaccessible)
    sex_placeholder = any("at least one of" in m for m in missing)

    def _auto_found() -> int:
        return sum(
            1 for n in auto_names if (sample_dir / n).is_file() and n not in inacc
        )

    def _x_found() -> int:
        return 1 if has_x and x_name not in inacc else 0

    def _y_found() -> int:
        return 1 if has_y and y_name not in inacc else 0

    if profile == "male":
        sex_expected = 2
        sex_found = _x_found() + _y_found()
    elif profile == "female":
        sex_expected = 1
        sex_found = _x_found()
    elif sex_mode == "both":
        sex_expected = 2
        sex_found = _x_found() + _y_found()
    elif sex_placeholder:
        # At least one sex chromosome required (auto/any, neither X nor Y present).
        sex_expected = 1
        sex_found = 1 if (_x_found() or _y_found()) else 0
    else:
        sex_expected = 0
        sex_found = 0

    auto_expected = len(AUTOSOMES)
    return auto_expected + sex_expected, _auto_found() + sex_found


def check_sample_dir(
    sample_dir: Path,
    context: str,
    *,
    sex_mode: SexChromMode,
    owner_uid: int,
    owner_gid: int,
    fix_permissions: bool,
    samples_base: Path,
) -> SampleCheckResult:
    required, profile = _required_h5_names(sample_dir, context, sex_mode)
    ctx = context.strip() or DEFAULT_CONTEXT
    x_name = h5_name("X", ctx)
    y_name = h5_name("Y", ctx)
    has_x = (sample_dir / x_name).is_file()
    has_y = (sample_dir / y_name).is_file()
    auto_names = _autosome_names(ctx)

    missing = [n for n in required if not (sample_dir / n).is_file()]

    if sex_mode == "any":
        auto_missing = [m for m in missing if m in auto_names]
        if auto_missing:
            missing = auto_missing
        elif not (has_x or has_y):
            missing = [f"at least one of {x_name}, {y_name}"]
        else:
            missing = []
            profile = "male" if has_y else "female"

    elif sex_mode == "auto" and profile == "unknown":
        auto_missing = [m for m in missing if m in auto_names]
        if not has_x and not has_y:
            missing = [*auto_missing, f"at least one of {x_name}, {y_name}"]
        elif has_y and not has_x:
            missing = [x_name]
            profile = "male"
        else:
            missing = auto_missing

    present_names = [n for n in required if (sample_dir / n).is_file()]
    inaccessible: List[str] = []
    fixed: List[str] = []

    _ensure_dir_traversable(
        sample_dir,
        uid=owner_uid,
        gid=owner_gid,
        fix=fix_permissions,
        stop_at=samples_base,
    )

    for name in present_names:
        h5_path = sample_dir / name
        ok, was_fixed = ensure_h5_accessible(
            h5_path,
            uid=owner_uid,
            gid=owner_gid,
            fix=fix_permissions,
            stop_at=samples_base,
        )
        if was_fixed:
            fixed.append(name)
        if not ok:
            inaccessible.append(name)

    expected, found = _compute_expected_and_found(
        sample_dir,
        auto_names=auto_names,
        x_name=x_name,
        y_name=y_name,
        has_x=has_x,
        has_y=has_y,
        profile=profile,
        sex_mode=sex_mode,
        missing=missing,
        inaccessible=inaccessible,
    )

    return SampleCheckResult(
        sample_id=sample_dir.name,
        sample_dir=sample_dir,
        sex_profile=profile,
        expected=expected,
        found=found,
        missing=missing,
        inaccessible=inaccessible,
        fixed=fixed,
    )


def list_sample_dirs(samples_base: Path) -> List[Path]:
    if not samples_base.is_dir():
        raise FileNotFoundError(f"samples base not found: {samples_base}")
    return sorted(
        (p.resolve() for p in samples_base.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )


def write_report(path: Path, results: Sequence[SampleCheckResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "sample_id",
                "sample_dir",
                "sex_profile",
                "status",
                "expected_h5",
                "found_h5",
                "missing_count",
                "missing_h5",
                "inaccessible_count",
                "inaccessible_h5",
                "fixed_count",
                "fixed_h5",
            ]
        )
        for r in results:
            w.writerow(
                [
                    r.sample_id,
                    str(r.sample_dir),
                    r.sex_profile,
                    "complete" if r.complete else "incomplete",
                    r.expected,
                    r.found,
                    len(r.missing),
                    ";".join(r.missing),
                    len(r.inaccessible),
                    ";".join(r.inaccessible),
                    len(r.fixed),
                    ";".join(r.fixed),
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Check all sample folders under /work/samples for required H5 files "
            f"and {DEFAULT_OWNER} read access."
        ),
    )
    p.add_argument(
        "--samples-base",
        default=DEFAULT_SAMPLES_BASE,
        help=f"Directory containing one folder per sample (default: {DEFAULT_SAMPLES_BASE})",
    )
    p.add_argument(
        "--context",
        default=DEFAULT_CONTEXT,
        help=f"H5 suffix context (default: {DEFAULT_CONTEXT})",
    )
    p.add_argument(
        "--owner",
        default=DEFAULT_OWNER,
        help=f"Unix user that must be able to read each H5 (default: {DEFAULT_OWNER})",
    )
    p.add_argument(
        "--no-fix-permissions",
        action="store_true",
        help="Do not chown/chmod; only report inaccessible files",
    )
    p.add_argument(
        "--sex-chromosomes-mode",
        choices=("auto", "both", "any"),
        default="auto",
        help="auto: infer male (X+Y) vs female (X only); both: always X+Y; any: X or Y (default: auto)",
    )
    p.add_argument(
        "--report",
        type=Path,
        help="Write CSV report to this path",
    )
    p.add_argument(
        "--show-missing-limit",
        type=int,
        default=8,
        help="Max issues listed per incomplete sample (default: 8)",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    samples_base = Path(args.samples_base).expanduser().resolve()
    sex_mode: SexChromMode = args.sex_chromosomes_mode
    fix_permissions = not args.no_fix_permissions

    try:
        owner_uid, owner_gid = _owner_uid_gid(args.owner)
    except KeyError:
        print(f"Unknown owner user: {args.owner!r}", file=sys.stderr)
        return 2

    if os.geteuid() not in (0, owner_uid):
        print(
            f"Warning: running as uid={os.geteuid()}; can only fix files owned by you "
            f"or use sudo for full chown to {args.owner}.",
            file=sys.stderr,
        )

    sample_dirs = list_sample_dirs(samples_base)
    if not sample_dirs:
        print(f"No sample directories under {samples_base}", file=sys.stderr)
        return 2

    results = [
        check_sample_dir(
            d,
            args.context,
            sex_mode=sex_mode,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            fix_permissions=fix_permissions,
            samples_base=samples_base,
        )
        for d in sample_dirs
    ]
    complete = [r for r in results if r.complete]
    incomplete = [r for r in results if not r.complete]
    profiles: dict[str, int] = {}
    total_fixed = 0
    for r in complete:
        profiles[r.sex_profile] = profiles.get(r.sex_profile, 0) + 1
    for r in results:
        total_fixed += len(r.fixed)

    print(f"Samples base: {samples_base}")
    print(f"Owner: {args.owner} (uid={owner_uid})  Fix permissions: {fix_permissions}")
    print(f"Samples checked: {len(results)}")
    print(f"Context: {args.context}  Sex chromosomes: {sex_mode}")
    print(f"Autosomes required: 1–22")
    print(f"Complete: {len(complete)}  Incomplete: {len(incomplete)}")
    if total_fixed:
        print(f"Permissions fixed on {total_fixed} file(s)")
    if profiles:
        print("Complete by profile: " + ", ".join(f"{k}={v}" for k, v in sorted(profiles.items())))

    limit = max(0, int(args.show_missing_limit))
    for r in incomplete:
        print(
            f"\nINCOMPLETE  {r.sample_id}  profile={r.sex_profile}  "
            f"({r.found}/{r.expected})  {r.sample_dir}"
        )
        for name in r.missing[:limit]:
            print(f"  missing: {name}")
        for name in r.inaccessible[:limit]:
            print(f"  not readable by {args.owner}: {name}")
        if limit:
            extra = len(r.missing) + len(r.inaccessible) - 2 * limit
            if extra > 0:
                print(f"  ... and {extra} more issue(s)")

    if args.report:
        write_report(args.report, results)
        print(f"\nWrote report: {args.report}")

    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
