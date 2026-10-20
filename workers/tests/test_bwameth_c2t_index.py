"""Clara fq2bam_meth requires the six bwa-meth C2T index siblings."""

from __future__ import annotations

from pathlib import Path

import pytest

from methyl_worker.parabricks_runner import (
    bwameth_c2t_index_paths,
    require_bwameth_c2t_index,
)


def test_require_bwameth_c2t_index_names_missing_paths(tmp_path: Path) -> None:
    fasta = tmp_path / "Homo_sapiens.GRCh38.dna.primary_assembly.fa"
    fasta.write_text(">1\nACGT\n", encoding="utf-8")
    present = fasta.with_name(fasta.name + ".bwameth.c2t")
    present.write_bytes(b"")

    missing = [path for path in bwameth_c2t_index_paths(fasta) if path != present]
    with pytest.raises(RuntimeError, match="bwa-meth C2T index missing") as exc:
        require_bwameth_c2t_index(fasta)
    message = str(exc.value)
    listed = {item.strip() for item in message.split(": ", 1)[1].split(",")}
    assert listed == {str(path) for path in missing}
    assert str(present) not in listed


def test_require_bwameth_c2t_index_accepts_all_six(tmp_path: Path) -> None:
    fasta = tmp_path / "ref.fa"
    fasta.write_text(">1\nACGT\n", encoding="utf-8")
    for path in bwameth_c2t_index_paths(fasta):
        path.write_bytes(b"")
    require_bwameth_c2t_index(fasta)
