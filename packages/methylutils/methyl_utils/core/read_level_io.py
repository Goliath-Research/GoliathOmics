"""Load and write read-level methylation pattern sidecar HDF5 files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np

SCHEMA_VERSION = "1.0.0"
PATTERN_ENCODING = "bitmask_msb_first"
GROUP_NAME = "read_level_patterns"


@dataclass(frozen=True)
class ReadLevelPatterns:
    """Read-level pattern histograms for one chromosome/context sidecar."""

    context: str
    tile_size: int
    tile_start_pos: np.ndarray
    tile_cpg_positions: np.ndarray
    tile_n_reads: np.ndarray
    pattern_tile_id: np.ndarray
    pattern_id: np.ndarray
    pattern_count: np.ndarray
    min_tile_reads: int = 1
    pattern_encoding: str = PATTERN_ENCODING
    schema_version: str = SCHEMA_VERSION

    @property
    def n_tiles(self) -> int:
        return int(self.tile_start_pos.size)

    def tile_histogram(self, tile_index: int) -> Dict[int, int]:
        """Return pattern_id -> count for one tile."""
        mask = self.pattern_tile_id == int(tile_index)
        if not np.any(mask):
            return {}
        ids = self.pattern_id[mask].astype(int)
        counts = self.pattern_count[mask].astype(int)
        return {int(i): int(c) for i, c in zip(ids, counts)}


def _import_h5py():
    try:
        import hdf5plugin  # noqa: F401
    except ImportError:
        pass
    import h5py

    return h5py


def load_read_level_patterns(path: Union[str, Path]) -> ReadLevelPatterns:
    """Load a `{chrom}-{ctx}.patterns.h5` sidecar file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Read-level pattern file not found: {p}")

    h5py = _import_h5py()
    with h5py.File(p, "r") as f:
        if GROUP_NAME not in f:
            raise ValueError(f"HDF5 missing group {GROUP_NAME!r}: {p}")
        grp = f[GROUP_NAME]
        required = (
            "tile_start_pos",
            "tile_cpg_positions",
            "tile_n_reads",
            "pattern_tile_id",
            "pattern_id",
            "pattern_count",
        )
        missing = [name for name in required if name not in grp]
        if missing:
            raise ValueError(f"HDF5 group {GROUP_NAME!r} missing datasets {missing}: {p}")

        context = str(grp.attrs.get("context", "CG"))
        tile_size = int(grp.attrs.get("tile_size", 4))
        min_tile_reads = int(grp.attrs.get("min_tile_reads", 1))
        pattern_encoding = str(grp.attrs.get("pattern_encoding", PATTERN_ENCODING))
        schema_version = str(grp.attrs.get("schema_version", SCHEMA_VERSION))

        return ReadLevelPatterns(
            context=context,
            tile_size=tile_size,
            tile_start_pos=np.asarray(grp["tile_start_pos"][:], dtype=np.uint32),
            tile_cpg_positions=np.asarray(grp["tile_cpg_positions"][:], dtype=np.uint32),
            tile_n_reads=np.asarray(grp["tile_n_reads"][:], dtype=np.uint32),
            pattern_tile_id=np.asarray(grp["pattern_tile_id"][:], dtype=np.uint32),
            pattern_id=np.asarray(grp["pattern_id"][:], dtype=np.uint16),
            pattern_count=np.asarray(grp["pattern_count"][:], dtype=np.uint32),
            min_tile_reads=min_tile_reads,
            pattern_encoding=pattern_encoding,
            schema_version=schema_version,
        )


def write_read_level_patterns(path: Union[str, Path], data: ReadLevelPatterns) -> Path:
    """Write a sidecar file (used by tests and fixture builders)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    h5py = _import_h5py()
    with h5py.File(p, "w") as f:
        grp = f.create_group(GROUP_NAME)
        grp.attrs["context"] = str(data.context)
        grp.attrs["tile_size"] = int(data.tile_size)
        grp.attrs["pattern_encoding"] = str(data.pattern_encoding)
        grp.attrs["min_tile_reads"] = int(data.min_tile_reads)
        grp.attrs["schema_version"] = str(data.schema_version)
        grp.create_dataset("tile_start_pos", data=np.asarray(data.tile_start_pos, dtype=np.uint32))
        grp.create_dataset(
            "tile_cpg_positions",
            data=np.asarray(data.tile_cpg_positions, dtype=np.uint32),
        )
        grp.create_dataset("tile_n_reads", data=np.asarray(data.tile_n_reads, dtype=np.uint32))
        grp.create_dataset("pattern_tile_id", data=np.asarray(data.pattern_tile_id, dtype=np.uint32))
        grp.create_dataset("pattern_id", data=np.asarray(data.pattern_id, dtype=np.uint16))
        grp.create_dataset("pattern_count", data=np.asarray(data.pattern_count, dtype=np.uint32))
    return p


def discover_pattern_files(
    sample_dir: Union[str, Path],
    chromosomes: Sequence[str],
    contexts: Sequence[str],
) -> List[Path]:
    """Return existing `{chrom}-{ctx}.patterns.h5` paths under a sample directory."""
    root = Path(sample_dir)
    found: List[Path] = []
    for chrom in chromosomes:
        for ctx in contexts:
            path = root / f"{chrom}-{ctx}.patterns.h5"
            if path.is_file():
                found.append(path)
    return found
