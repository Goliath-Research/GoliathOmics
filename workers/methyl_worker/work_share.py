"""Make ``/work`` artifacts usable across fleet hosts with different numeric UIDs.

Sisters mount the same NFS tree as local users all named ``ubuntu`` but with
distinct numeric uids. Docker-as-root (Clara often ignores ``--user``) leaves
``root:root`` ``0644`` / ``0755`` objects. A later writer on another host then
gets ``EACCES`` on extract logs, QC JSON, and manifests — not only immediately
after align.

Helpers here cover both sides:

- **Prevention:** ``docker_umask_*`` so container-created files are ``0666`` /
  ``0777`` even when owned by root; ``share_work_tree`` then does **one**
  ``chmod -R a+rwX`` via Docker-as-root when local chmod hits ``EPERM``.
- **Write path:** ``ensure_work_writable`` / ``open_work`` / ``append_work_text``
  share the parent tree and retry once on ``PermissionError``.
- **Identity:** Align containers use ``--user 1000:1000`` (override
  ``METHYL_ALIGN_DOCKER_USER``) so sister VMs share one numeric owner. Clara
  may still ignore ``--user``; umask + ``share_work_tree`` remain the fallback.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

_KNOWN_WORK_ROOTS = (Path("/work"), Path("/lambda/nfs/Work"))
T = TypeVar("T")


def _docker_bin() -> str:
    return shutil.which("docker") or "docker"


def _chmod_image() -> str:
    return (os.environ.get("METHYL_SHARE_CHMOD_IMAGE") or "").strip() or "alpine:3.20"


def work_roots() -> tuple[Path, ...]:
    """Resolved ``/work``-equivalent roots (NFS bind mounts included)."""
    roots: list[Path] = []
    extra = (os.environ.get("METHYL_WORK_ROOT") or "").strip()
    if extra:
        try:
            roots.append(Path(extra).expanduser().resolve())
        except OSError:
            roots.append(Path(extra).expanduser())
    for candidate in _KNOWN_WORK_ROOTS:
        try:
            if candidate.exists():
                roots.append(candidate.resolve())
        except OSError:
            continue
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return tuple(out)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def align_docker_user() -> str:
    """Docker ``--user`` for Align/Extract writes on shared NFS ``/work``.

    Prefer ``1000:1000`` so every sister sees the same owner (hosts are all
    named ``ubuntu`` but have different numeric uids). Override with
    ``METHYL_ALIGN_DOCKER_USER=uid:gid``. Clara images may still ignore
    ``--user`` and write as root; pair with ``docker_umask_*``.
    """
    explicit = (os.environ.get("METHYL_ALIGN_DOCKER_USER") or "").strip()
    if explicit:
        return explicit
    return "1000:1000"


def docker_umask_prefix() -> tuple[str, str]:
    """``docker run`` flags placed immediately before the image name.

    Overrides the image ``ENTRYPOINT`` so ``docker_umask_suffix`` can run
    ``umask`` then ``exec`` the real tool. NVIDIA Clara images often ignore
    ``--user`` and write as root; umask ``000`` still leaves those files
    other-writable for sister hosts.
    """
    return ("--entrypoint", "sh")


def docker_umask_suffix(*container_argv: str, umask: str = "000") -> list[str]:
    """Argv after the image: ``sh -c 'umask …; exec \"$@\"' sh <tool> …``."""
    if not container_argv:
        raise ValueError("docker_umask_suffix requires a command to exec")
    return ["-c", f"umask {umask}; exec \"$@\"", "sh", *container_argv]


def _share_work_path_via_docker(path: Path) -> bool:
    """chmod via Docker as root when the host uid cannot change ownership bits."""
    if not path.exists():
        return False
    mode_flag = "a+rwx" if path.is_dir() else "a+rw"
    parent = str(path.parent.resolve())
    target = str(path.resolve())
    cmd = [
        _docker_bin(),
        "run",
        "--rm",
        "--user",
        "0:0",
        "-v",
        f"{parent}:{parent}",
        _chmod_image(),
        "chmod",
        mode_flag,
        target,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("share_work_path docker chmod failed for %s: %s", path, exc)
        return False
    if proc.returncode != 0:
        logger.warning(
            "share_work_path docker chmod rc=%s for %s: %s",
            proc.returncode,
            path,
            (proc.stderr or proc.stdout or "").strip()[:300],
        )
        return False
    return True


def _share_tree_via_docker(root: Path) -> bool:
    """One ``chmod -R a+rwX`` as root — not one container per file."""
    if not root.exists():
        return False
    target = str(root.resolve())
    cmd = [
        _docker_bin(),
        "run",
        "--rm",
        "--user",
        "0:0",
        "-v",
        f"{target}:{target}",
        _chmod_image(),
        "chmod",
        "-R",
        "a+rwX",
        target,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("share_work_tree docker chmod -R failed for %s: %s", root, exc)
        return False
    if proc.returncode != 0:
        logger.warning(
            "share_work_tree docker chmod -R rc=%s for %s: %s",
            proc.returncode,
            root,
            (proc.stderr or proc.stdout or "").strip()[:300],
        )
        return False
    return True


def share_work_path(path: Path | str) -> None:
    """Make a ``/work`` artifact usable across fleet hosts with different UIDs.

    Open group/other read-write (files) and rwx (dirs). Falls back to
    Docker-as-root chmod when the local uid cannot change mode.
    """
    p = Path(path)
    try:
        mode = p.stat().st_mode
    except OSError:
        return
    want = 0o777 if p.is_dir() else 0o666
    try:
        p.chmod(mode | want)
        return
    except OSError as exc:
        if _share_work_path_via_docker(p):
            return
        logger.warning("share_work_path chmod failed for %s: %s", p, exc)


def share_work_ancestors(path: Path | str) -> None:
    """Share existing parent directories so this uid can create/overwrite ``path``.

    Under a known ``/work`` root, walk up to that root. Elsewhere (unit tests)
    share only the immediate parent.
    """
    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    parent = resolved.parent
    stop: Path | None = None
    for root in work_roots():
        if parent == root or _is_under(parent, root):
            stop = root
            break
    current = parent
    n = 0
    while True:
        if current.exists():
            share_work_path(current)
        n += 1
        if stop is None or current == stop or current.parent == current or n > 32:
            break
        current = current.parent


def share_work_tree(root: Path | str, *, max_entries: int = 50_000) -> int:
    """Open a directory tree for fleet co-writers. Returns paths visited.

    Local ``chmod`` first. If any path returns ``EPERM``, one Docker
    ``chmod -R a+rwX`` covers the rest (Clara leftover root-owned files).
    """
    root_p = Path(root)
    if not root_p.exists():
        return 0
    failed = False
    n = 0

    def _try(p: Path) -> None:
        nonlocal failed, n
        n += 1
        try:
            mode = p.stat().st_mode
            want = 0o777 if p.is_dir() else 0o666
            p.chmod(mode | want)
        except OSError:
            failed = True

    _try(root_p)
    if root_p.is_dir():
        for dirpath, dirnames, filenames in os.walk(root_p):
            for name in dirnames:
                _try(Path(dirpath) / name)
                if n >= max_entries:
                    break
            else:
                for name in filenames:
                    _try(Path(dirpath) / name)
                    if n >= max_entries:
                        break
            if n >= max_entries:
                break
    if failed:
        if _share_tree_via_docker(root_p):
            logger.info("share_work_tree docker chmod -R ok for %s (%s paths)", root_p, n)
        else:
            logger.warning("share_work_tree docker chmod -R failed for %s", root_p)
    return n


def ensure_work_writable(path: Path | str) -> Path:
    """Share ancestors and an existing path so this uid can create/overwrite it."""
    p = Path(path)
    parent = p.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        share_work_ancestors(p)
        parent.mkdir(parents=True, exist_ok=True)
    share_work_ancestors(p)
    if p.exists():
        share_work_path(p)
    return p


@contextmanager
def open_work(path: Path | str, mode: str = "r", **kwargs):
    """``Path.open`` with one share+retry on ``PermissionError`` for writes."""
    p = Path(path)
    writing = any(flag in mode for flag in "wax+")
    if writing:
        ensure_work_writable(p)
    try:
        with p.open(mode, **kwargs) as fh:
            yield fh
            return
    except PermissionError:
        if not writing:
            raise
        ensure_work_writable(p)
        with p.open(mode, **kwargs) as fh:
            yield fh


def append_work_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> Path:
    """Append text (adds a trailing newline) and share the file for sisters."""
    p = Path(path)
    payload = text if text.endswith("\n") else text + "\n"
    with open_work(p, "a", encoding=encoding) as fh:
        fh.write(payload)
    if p.exists():
        share_work_path(p)
    return p


def write_work_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> Path:
    """Overwrite a text file and share it for sisters."""
    p = Path(path)
    with open_work(p, "w", encoding=encoding) as fh:
        fh.write(text)
    if p.exists():
        share_work_path(p)
    return p


def replace_work_file(src: Path | str, dest: Path | str) -> Path:
    """``Path.replace`` with share+retry so a foreign-uid dest can be overwritten."""
    src_p = Path(src)
    dest_p = Path(dest)
    ensure_work_writable(dest_p)

    def _replace() -> None:
        src_p.replace(dest_p)

    try:
        _replace()
    except PermissionError:
        ensure_work_writable(dest_p)
        try:
            _replace()
        except PermissionError:
            if dest_p.exists():
                dest_p.unlink()
            _replace()
    if dest_p.exists():
        share_work_path(dest_p)
    return dest_p


def run_with_work_write(fn: Callable[[], T], *paths: Path | str) -> T:
    """Run ``fn`` after sharing ``paths``; retry once on ``PermissionError``."""
    ps = [Path(p) for p in paths]
    for p in ps:
        ensure_work_writable(p)
    try:
        result = fn()
    except PermissionError:
        for p in ps:
            ensure_work_writable(p)
        result = fn()
    for p in ps:
        if p.exists():
            share_work_path(p)
    return result
