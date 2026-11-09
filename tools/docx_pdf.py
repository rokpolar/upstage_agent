"""Word→PDF 호환 실행 진입점. 구현은 app/adapters/docx_pdf.py에 있다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    import runpy

    runpy.run_module("app.adapters.docx_pdf", run_name="__main__")
else:
    from app.adapters.docx_pdf import *
