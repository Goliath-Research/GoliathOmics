"""Real-data: a designated cohort (e.g. healthy, PCa) loads as real samples.

Exercises the group registry and multi-sample real H5 loading used by
cohort-level tools (centroid, clustering). Skips when the group is not mounted;
see docs/reference/test-data-registry.md.
"""

from __future__ import annotations

import numpy as np
import pytest

from methyl_utils import MethylSample
from methyl_utils.testing import require_reference_group


@pytest.mark.real_data
@pytest.mark.parametrize("group_name", ["healthy", "PCa"])
def test_reference_group_samples_load_cg(group_name: str) -> None:
    group = require_reference_group(group_name, min_samples=2)

    loaded = 0
    for sample_dir in group.sample_dirs:
        h5 = sample_dir / "21-CG.h5"
        if not h5.is_file():
            continue
        ms = MethylSample.load_from_h5(h5)
        pos = np.asarray(ms.pos)
        assert pos.size > 0
        loaded += 1

    assert loaded >= 2, f"expected >= 2 loadable samples in group {group_name!r}"
