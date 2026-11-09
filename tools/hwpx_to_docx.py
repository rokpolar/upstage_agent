"""HWPX→Word 양식 변환 호환 실행 진입점. 구현은 app/adapters/hwpx_to_docx.py에 있다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    import runpy

    runpy.run_module("app.adapters.hwpx_to_docx", run_name="__main__")
else:
    from app.adapters.hwpx_to_docx import *
