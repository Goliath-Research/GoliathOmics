"""Load and write per-read CpG haplotype sidecar HDF5 files ({chrom}-CG.mhap.h5)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Sequence, Tuple, Union

import numpy as np

SCHEMA_VERSION = "1.0.0"
GROUP_NAME = "mhap"


@dataclass(frozen=True)
class MhapStore:
    """Variable-length per-read CpG haplotypes for one chromosome."""

    chrom: str
    context: str
    read_start: np.ndarray
    read_strand: np.ndarray
    n_cpg: np.ndarray
    cpg_pos: np.ndarray
    meth: np.ndarray
    schema_version: str = SCHEMA_VERSION

    @property
    def n_reads(self) -> int:
        return int(self.read_start.size)

    def iter_reads(self) -> Iterator[Tuple[int, int, np.ndarray, np.ndarray]]:
        """Yield (start, strand, positions, meth_bits) per read."""
        off = 0
        for i in range(self.n_reads):
            n = int(self.n_cpg[i])
            yield (
                int(self.read_start[i]),
                int(self.read_strand[i]),
                self.cpg_pos[off : off + n],
                self.meth[off : off + n],
            )
            off += n


def _import_h5py():
    try:
        import hdf5plugin  # noqa: F401
    except ImportError:
        pass
    import h5py

    return h5py


def load_mhap_store(path: Union[str, Path]) -> MhapStore:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Haplotype store not found: {p}")
    h5py = _import_h5py()
    with h5py.File(p, "r") as f:
        if GROUP_NAME not in f:
            raise ValueError(f"HDF5 missing group {GROUP_NAME!r}: {p}")
        grp = f[GROUP_NAME]
        required = ("read_start", "read_strand", "n_cpg", "cpg_pos", "meth")
        missing = [name for name in required if name not in grp]
        if missing:
            raise ValueError(f"HDF5 group {GROUP_NAME!r} missing datasets {missing}: {p}")
        return MhapStore(
            chrom=str(grp.attrs.get("chrom", "")),
            context=str(grp.attrs.get("context", "CG")),
            read_start=np.asarray(grp["read_start"][:], dtype=np.int32),
            read_strand=np.asarray(grp["read_strand"][:], dtype=np.int8),
            n_cpg=np.asarray(grp["n_cpg"][:], dtype=np.int32),
            cpg_pos=np.asarray(grp["cpg_pos"][:], dtype=np.int32),
            meth=np.asarray(grp["meth"][:], dtype=np.uint8),
            schema_version=str(grp.attrs.get("schema_version", SCHEMA_VERSION)),
        )


def write_mhap_store(path: Union[str, Path], data: MhapStore) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    h5py = _import_h5py()
    with h5py.File(p, "w") as f:
        grp = f.create_group(GROUP_NAME)
        grp.attrs["schema_version"] = str(data.schema_version)
        grp.attrs["context"] = str(data.context)
        grp.attrs["chrom"] = str(data.chrom)
        grp.create_dataset("read_start", data=np.asarray(data.read_start, dtype=np.int32))
        grp.create_dataset("read_strand", data=np.asarray(data.read_strand, dtype=np.int8))
        grp.create_dataset("n_cpg", data=np.asarray(data.n_cpg, dtype=np.int32))
        grp.create_dataset("cpg_pos", data=np.asarray(data.cpg_pos, dtype=np.int32))
        grp.create_dataset("meth", data=np.asarray(data.meth, dtype=np.uint8))
    return p


def discover_mhap_files(
    sample_dir: Union[str, Path],
    chromosomes: Sequence[str],
    context: str = "CG",
) -> List[Path]:
    root = Path(sample_dir)
    found: List[Path] = []
    for chrom in chromosomes:
        path = root / f"{chrom}-{context}.mhap.h5"
        if path.is_file():
            found.append(path)
    return found
