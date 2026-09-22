from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from methyl_detector.utils.multiclass_merge import check_detection_dirs_have_dmps


class _CfgNoOutput(BaseModel):
    name: str = "x"


def test_check_detection_dirs_rejects_missing_output_dir_dict():
    with pytest.raises(ValueError, match="missing required key"):
        check_detection_dirs_have_dmps([({"name": "x"}, "label1")])


def test_check_detection_dirs_rejects_missing_output_dir_pydantic():
    with pytest.raises(ValueError, match="missing required field"):
        check_detection_dirs_have_dmps([(_CfgNoOutput(), "label1")])


def test_check_detection_dirs_rejects_unsupported_type():
    with pytest.raises(TypeError, match="Unsupported config type"):
        check_detection_dirs_have_dmps([((1, 2, 3), "label1")])


def test_check_detection_dirs_accepts_path_like(tmp_path: Path):
    out = tmp_path / "det"
    out.mkdir()
    missing = check_detection_dirs_have_dmps([(str(out), "label1")])
    assert len(missing) == 1
    assert missing[0][1] == "label1"
