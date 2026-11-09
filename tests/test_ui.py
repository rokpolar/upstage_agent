import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["ui_smoke.mjs", "gyeolui_ui.mjs", "pumui_ui.mjs"])
def test_es_modules_restore_and_progress(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js가 필요한 화면 모듈 검증")
    result = subprocess.run(
        [node, str(Path(__file__).with_name(script))],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
