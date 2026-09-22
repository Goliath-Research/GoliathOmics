"""Access designated real reference samples in tests, skipping when unavailable.

Real-data tests should be marked ``@pytest.mark.real_data`` and obtain their
sample via :func:`require_reference_sample` / :func:`require_reference_group`,
which call ``pytest.skip`` when the sample is not mounted (e.g. on hosted CI
agents that do not mount ``/work``). See
``docs/reference/test-data-registry.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from ..analyte_profiles import normalize_primary_analyte
from ..test_data_registry import (
    TestDataRegistry,
    TestSampleGroup,
    TestSampleRef,
    load_test_data_registry,
    resolve_sample_dir,
    sample_has_h5,
)


@dataclass(frozen=True)
class ResolvedReferenceSample:
    key: str
    sample_dir: Path
    ref: TestSampleRef

    def h5_path(self, chromosome: str, context: str = "CG") -> Path:
        return self.sample_dir / f"{chromosome}-{context}.h5"


@dataclass(frozen=True)
class ResolvedReferenceGroup:
    name: str
    sample_dirs: List[Path]
    group: TestSampleGroup


@lru_cache(maxsize=1)
def get_registry() -> TestDataRegistry:
    return load_test_data_registry()


def _match_sample_key(registry: TestDataRegistry, analyte: str) -> Optional[str]:
    if not registry.samples:
        return None
    canonical = normalize_primary_analyte(analyte) or analyte
    for key, ref in registry.samples.items():
        if normalize_primary_analyte(key) == canonical:
            return key
        if ref.analyte and normalize_primary_analyte(ref.analyte) == canonical:
            return key
    if analyte in registry.samples:
        return analyte
    return None


def reference_sample(analyte: str) -> Optional[ResolvedReferenceSample]:
    """Return a resolved, on-disk reference sample for an analyte, or None."""
    registry = get_registry()
    key = _match_sample_key(registry, analyte)
    if key is None or not registry.samples:
        return None
    ref = registry.samples[key]
    sample_dir = resolve_sample_dir(ref.sample_dir)
    if sample_dir is None or not sample_has_h5(sample_dir):
        return None
    return ResolvedReferenceSample(key=key, sample_dir=sample_dir, ref=ref)


def available_reference_sample(analyte: str) -> bool:
    return reference_sample(analyte) is not None


def require_reference_sample(
    analyte: str,
    *,
    chromosome: Optional[str] = None,
    context: str = "CG",
) -> ResolvedReferenceSample:
    """Return a resolved reference sample or ``pytest.skip`` when unavailable."""
    import pytest

    resolved = reference_sample(analyte)
    if resolved is None:
        pytest.skip(
            f"no real reference sample for analyte {analyte!r} "
            f"(set METHYL_TEST_DATA_CONFIG or site 'testing' block; "
            f"see docs/reference/test-data-registry.md)"
        )
    if chromosome is not None:
        h5 = resolved.h5_path(chromosome, context)
        if not h5.is_file():
            pytest.skip(f"reference sample {resolved.key!r} missing {h5.name}")
    return resolved


def reference_group(name: str) -> Optional[ResolvedReferenceGroup]:
    registry = get_registry()
    if not registry.groups or name not in registry.groups:
        return None
    group = registry.groups[name]
    dirs: List[Path] = []
    for raw in group.sample_dirs or []:
        d = resolve_sample_dir(raw)
        if d is not None and sample_has_h5(d):
            dirs.append(d)
    if not dirs:
        return None
    return ResolvedReferenceGroup(name=name, sample_dirs=dirs, group=group)


def require_reference_group(name: str, *, min_samples: int = 1) -> ResolvedReferenceGroup:
    """Return a resolved reference group or ``pytest.skip`` when unavailable."""
    import pytest

    resolved = reference_group(name)
    if resolved is None or len(resolved.sample_dirs) < min_samples:
        pytest.skip(
            f"real reference group {name!r} with >= {min_samples} sample(s) not available "
            f"(see docs/reference/test-data-registry.md)"
        )
    return resolved
