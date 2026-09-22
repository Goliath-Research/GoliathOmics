from __future__ import annotations

import pytest

from methyl_predictor.project_resolver import _control_disease_side_to_dict


def test_control_disease_side_to_dict_rejects_unsupported_type():
    with pytest.raises(TypeError, match="Expected control/disease side"):
        _control_disease_side_to_dict(42)
