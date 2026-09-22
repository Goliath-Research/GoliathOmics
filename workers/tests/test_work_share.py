"""Fleet /work share: write-retry belt and one recursive docker chmod."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from methyl_worker.work_share import (
    append_work_text,
    docker_umask_prefix,
    docker_umask_suffix,
    ensure_work_writable,
    run_with_work_write,
    share_work_path,
    share_work_tree,
    write_work_text,
)


def test_append_work_text_retries_after_eacces(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "sample.methyl_extract.log"
    original = Path.open
    state = {"n": 0}

    def flaky(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self.name.endswith(".methyl_extract.log") and "a" in str(mode):
            state["n"] += 1
            if state["n"] == 1:
                raise PermissionError("EACCES")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky)
    append_work_text(log_path, "COMMAND: extract")
    assert state["n"] == 2
    assert "COMMAND: extract" in log_path.read_text(encoding="utf-8")


def test_write_work_text_retries_qc_json(tmp_path: Path, monkeypatch) -> None:
    qc_path = tmp_path / "alignment_qc" / "sample.json"
    original = Path.open
    state = {"n": 0}

    def flaky(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self.name.endswith(".json") and "w" in str(mode):
            state["n"] += 1
            if state["n"] == 1:
                raise PermissionError("EACCES")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky)
    write_work_text(qc_path, '{"ok": true}\n')
    assert state["n"] == 2
    assert qc_path.read_text(encoding="utf-8") == '{"ok": true}\n'


def test_run_with_work_write_retries_fn(tmp_path: Path) -> None:
    dest = tmp_path / "out.json"
    dest.write_text("stale\n", encoding="utf-8")
    dest.chmod(0o600)
    state = {"n": 0}

    def write_once() -> str:
        state["n"] += 1
        if state["n"] == 1:
            raise PermissionError("EACCES")
        dest.write_text("fresh\n", encoding="utf-8")
        return "ok"

    assert run_with_work_write(write_once, dest) == "ok"
    assert state["n"] == 2
    assert dest.read_text(encoding="utf-8") == "fresh\n"
    assert dest.stat().st_mode & 0o006 == 0o006


def test_ensure_work_writable_opens_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "locked.bin"
    f.write_bytes(b"x")
    f.chmod(0o600)
    ensure_work_writable(f)
    assert f.stat().st_mode & 0o006 == 0o006


def test_share_work_tree_uses_one_recursive_docker_chmod(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "sample"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.bin").write_bytes(b"a")
    (root / "b.bin").write_bytes(b"b")

    def boom_chmod(self, mode):  # noqa: ARG001
        raise PermissionError("EPERM")

    monkeypatch.setattr(Path, "chmod", boom_chmod)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls.append(list(cmd))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    n = share_work_tree(root)
    assert n >= 4
    assert len(calls) == 1
    cmd = calls[0]
    assert "-R" in cmd
    assert "a+rwX" in cmd
    assert str(root.resolve()) in cmd


def test_share_work_path_still_opens_mode_600(tmp_path: Path) -> None:
    f = tmp_path / "out.bin"
    f.write_bytes(b"x")
    f.chmod(0o600)
    share_work_path(f)
    assert f.stat().st_mode & 0o006 == 0o006


def test_docker_umask_wraps_pbrun() -> None:
    prefix = docker_umask_prefix()
    suffix = docker_umask_suffix("pbrun", "fq2bam_meth", "--gpusort")
    assert prefix == ("--entrypoint", "sh")
    assert suffix[0] == "-c"
    assert "umask 000" in suffix[1]
    assert suffix[2:] == ["sh", "pbrun", "fq2bam_meth", "--gpusort"]


def test_stale_append_retries_eacces_and_shares(tmp_path: Path, monkeypatch) -> None:
    """ImportError fallback must share + retry, not a bare Path.open('a')."""
    from types import SimpleNamespace

    from methyl_worker.work_share_compat import stale_append_work_text

    log_path = tmp_path / "align.linear.parabricks" / "sample.fq2bam_meth.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("prior\n", encoding="utf-8")
    log_path.chmod(0o600)

    original = Path.open
    state = {"n": 0}

    def flaky(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self.name.endswith(".fq2bam_meth.log") and "a" in str(mode):
            state["n"] += 1
            if state["n"] == 1:
                raise PermissionError("EACCES")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky)
    stale = SimpleNamespace(
        share_work_path=share_work_path,
        share_work_tree=share_work_tree,
        share_work_ancestors=None,
        open_work=None,
        append_work_text=None,
    )
    stale_append_work_text(log_path, "COMMAND: pbrun", work_share=stale)
    assert state["n"] == 2
    assert "COMMAND: pbrun" in log_path.read_text(encoding="utf-8")
    assert log_path.stat().st_mode & 0o006 == 0o006


def test_stale_append_uses_open_work_when_present(tmp_path: Path) -> None:
    from methyl_worker.work_share import open_work
    from methyl_worker.work_share_compat import stale_append_work_text

    log_path = tmp_path / "sample_prep.log"
    stale_append_work_text(
        log_path,
        "line",
        work_share=__import__("methyl_worker.work_share", fromlist=["open_work"]),
    )
    assert "line" in log_path.read_text(encoding="utf-8")
    assert log_path.stat().st_mode & 0o006 == 0o006
    assert open_work is not None
