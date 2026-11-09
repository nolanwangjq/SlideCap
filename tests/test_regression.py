"""Opt-in full synthetic regression (includes the original eval.py)."""

from pathlib import Path
import os
import subprocess
import sys
import pytest


@pytest.mark.skipif(
    not os.environ.get("SLIDECAP_SYNTHETIC"),
    reason="Set SLIDECAP_SYNTHETIC to a generated fixture directory",
)
def test_original_eval_and_web_regression(tmp_path):
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts/regression.py"),
            os.environ["SLIDECAP_SYNTHETIC"],
            "--output",
            str(tmp_path),
        ],
        check=True,
    )
