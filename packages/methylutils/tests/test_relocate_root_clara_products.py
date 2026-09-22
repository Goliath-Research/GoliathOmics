"""Tests for scripts/relocate_root_clara_products.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "relocate_root_clara_products.py"
_SPEC = importlib.util.spec_from_file_location("relocate_root_clara_products", _SCRIPT)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_relocate_moves_root_clara_products_skips_existing_arm(tmp_path: Path) -> None:
    root = tmp_path / "S1"
    arm = root / "align.linear.parabricks"
    arm.mkdir(parents=True)
    (root / "S1.bam").write_bytes(b"NEW")
    (root / "S1.qc-metrics.tar").write_bytes(b"TAR")
    (root / "S1.json").write_text("{}", encoding="utf-8")
    (root / "1-CG.h5").write_bytes(b"H5")
    (root / "S1_1.fastq.gz").write_bytes(b"FQ")
    (arm / "S1.bam").write_bytes(b"OLD")
    caas = root / ".caas" / "keep.txt"
    caas.parent.mkdir()
    caas.write_text("caas", encoding="utf-8")

    record = _MOD.relocate_sample_root(root, dry_run=False)
    skipped_src = {row["src"] for row in record["skipped"]}
    moved_dest = {row["dest"] for row in record["moved"]}
    assert str(root / "S1.bam") in skipped_src
    assert (arm / "S1.bam").read_bytes() == b"OLD"
    assert str(arm / "S1.qc-metrics.tar") in moved_dest
    assert (arm / "S1.qc-metrics.tar").read_bytes() == b"TAR"
    assert (arm / "S1.json").is_file()
    assert (arm / "1-CG.h5").read_bytes() == b"H5"
    assert (root / "S1_1.fastq.gz").is_file()
    assert caas.is_file()


def test_main_refuses_live_work_samples() -> None:
    rc = _MOD.main(["--samples-base", "/work/samples"])
    assert rc == 2
