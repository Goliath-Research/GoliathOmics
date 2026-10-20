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

import grp
import logging
import os
import re
import shutil
import socket
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


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return cleaned or "x"


def _machine_id() -> str:
    try:
        text = Path("/etc/machine-id").read_text(encoding="utf-8")
    except OSError:
        return ""
    return _safe_component(text.strip()[:32])


def _shared_group() -> str:
    """Group name when ``GOLIATH_SHARED_GROUP`` exists on this machine."""
    group = (os.environ.get("GOLIATH_SHARED_GROUP") or "").strip()
    if not group:
        return ""
    try:
        grp.getgrnam(group)
    except KeyError:
        return ""
    return group


def publisher_id() -> str:
    """Node id used in staging directory names.

    ``GOLIATH_NODE_ID`` is the provider-neutral name. A RunPod adapter sets
    it from ``RUNPOD_POD_ID`` before the worker starts. Otherwise the
    hostname is used, then ``/etc/machine-id``.
    """
    raw = (os.environ.get("GOLIATH_NODE_ID") or "").strip()
    if not raw:
        raw = socket.gethostname().split(".")[0]
    cleaned = _safe_component(raw) if raw else ""
    if cleaned and cleaned != "x":
        return cleaned
    return _machine_id() or "node"


def _work_umask() -> int:
    """``0002`` with a shared group, otherwise ``0022``."""
    if _shared_group():
        return 0o002
    return 0o022


def _explicit_mode(path: Path) -> int:
    """Directory 2775/0755 or file 0664/0644, matching the shell start."""
    if path.is_dir():
        return 0o2775 if _shared_group() else 0o0755
    return 0o0664 if _shared_group() else 0o0644


def completion_marker(dest: Path | str) -> Path:
    """Sibling ``<name>.complete`` written after the output rename returns."""
    path = Path(dest)
    return path.parent / f"{path.name}.complete"


def _device(path: Path) -> int:
    return path.stat().st_dev


def _clear_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _rename_over(src: Path, dest: Path) -> None:
    """Same-filesystem rename. Replaces an existing file or directory."""
    if dest.exists() and dest.is_dir():
        displaced = dest.with_name(f"{dest.name}.replacing-{os.getpid()}")
        os.rename(dest, displaced)
        try:
            os.rename(src, dest)
        except OSError:
            os.rename(displaced, dest)
            raise
        shutil.rmtree(displaced, ignore_errors=True)
        return
    os.rename(src, dest)


def _copy_readable(src: Path, dst: Path) -> None:
    """Copy bytes without copying mode bits.

    ``shutil.copy2`` finishes with ``copystat`` (``utime`` then ``chmod``).
    geesefs and a Runpod global volume return EPERM from those calls, and the
    copy aborts after the bytes are already written. Callers set a
    world-readable umask so the new files stay readable when the mode cannot
    be changed later.
    """
    if src.is_dir():
        dst.mkdir()
        for child in src.iterdir():
            _copy_readable(child, dst / child.name)
        return
    shutil.copyfile(src, dst)


def _write_completion_marker(dest: Path) -> Path:
    """Commit point after rename.

    Some shared volumes do not support atomic rename. A Runpod global volume
    is one (https://docs.runpod.io/storage/globalvolume/overview); POSIX
    filesystems and many NFS servers do. Readers on a non-atomic volume wait
    for this marker. It is a small file in ``dest``'s directory, written only
    after ``os.rename`` returns.
    """
    marker = completion_marker(dest)
    tmp = dest.parent / f".{dest.name}.complete.tmp-{os.getpid()}"
    tmp.write_text(dest.name + "\n", encoding="utf-8")
    os.rename(tmp, marker)
    try:
        share_work_path(marker)
    except OSError as exc:
        logger.warning("completion marker chmod failed for %s: %s", marker, exc)
    return marker


def publish_to_work(local_src: Path | str, dest: Path | str, *, task: str = "task") -> Path:
    """Publish a workflow output onto ``/work`` so every pod can read it.

    Workflow steps may compute on local scratch and then call this. The one
    copy from local scratch lands in the sibling ``<dest>.tmp-<node>-<task>``
    on ``dest``'s filesystem. ``os.rename`` then publishes ``dest``. A
    ``<dest>.complete`` marker is written last because some shared volumes
    do not provide atomic rename.

    A source that is already on ``dest``'s filesystem is renamed, not copied
    again. The cross-filesystem copy uses umask ``0022`` (``0002`` when
    ``GOLIATH_SHARED_GROUP`` exists) and does not copy mode bits. ``chmod``
    to 0644/0664 or 0755/2775 is then attempted. A volume that rejects chmod
    logs a warning and still returns ``dest``.
    """
    src_p = Path(local_src)
    dest_p = Path(dest)
    if not src_p.exists():
        raise FileNotFoundError(src_p)
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    staging = dest_p.with_name(
        f"{dest_p.name}.tmp-{publisher_id()}-{_safe_component(task)}"
    )
    if staging.resolve() == src_p.resolve():
        raise RuntimeError(f"refusing to publish {src_p} onto itself")
    _clear_path(staging)
    same_fs = _device(src_p) == _device(dest_p.parent)
    if same_fs:
        os.rename(src_p, staging)
    else:
        previous_umask = os.umask(_work_umask())
        try:
            _copy_readable(src_p, staging)
        finally:
            os.umask(previous_umask)
    try:
        _rename_over(staging, dest_p)
    except Exception:
        if same_fs and staging.exists() and not src_p.exists():
            try:
                os.rename(staging, src_p)
            except OSError:
                logger.warning("could not restore %s after a failed publish", src_p)
        else:
            _clear_path(staging)
        raise
    _write_completion_marker(dest_p)
    try:
        dest_p.chmod(_explicit_mode(dest_p))
    except OSError as exc:
        logger.warning("post-publish chmod failed for %s: %s", dest_p, exc)
    try:
        if dest_p.is_dir():
            share_work_tree(dest_p)
        else:
            share_work_path(dest_p)
    except OSError as exc:
        logger.warning("post-publish share failed for %s: %s", dest_p, exc)
    return dest_p


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
