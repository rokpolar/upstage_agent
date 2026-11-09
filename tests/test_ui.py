import shutil
import subprocess
from pathlib import Path

import pytest


def test_es_modules_restore_and_progress():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js가 필요한 화면 모듈 검증")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("ui_smoke.mjs"))],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
