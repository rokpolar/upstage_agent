import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "tool",
    [
        "run_api",
        "harness",
        "gyeolui",
        "select_vendor",
        "grouping",
        "fill_form",
        "hwpx_preview",
        "render_pumui",
    ],
)
def test_legacy_commands_help(tool):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / f"{tool}.py"), "--help"],
        cwd=ROOT.parent,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_harness_no_input_output(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "tools/harness.py",
            "results/q04.json",
            "--no-input",
            "--out",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.glob("*.json"))


def test_gyeolui_cli(tmp_path):
    fixture = next((ROOT / "examples/gyeolui").glob("*.json"))
    result = subprocess.run(
        [sys.executable, "tools/gyeolui.py", str(fixture), "--out", str(tmp_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.glob("*.json"))
