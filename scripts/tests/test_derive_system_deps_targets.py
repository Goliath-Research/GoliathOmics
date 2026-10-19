from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_derive_from_goliath_test():
    out = subprocess.check_output(
        [sys.executable, str(ROOT / "scripts/derive_system_deps_targets.py")],
        text=True,
    ).strip()
    assert out == "ubuntu-22.04-amd64"
