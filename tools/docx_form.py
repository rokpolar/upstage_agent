"""Word 양식 어댑터의 호환 진입점."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    import runpy

    runpy.run_module("app.adapters.docx_form", run_name="__main__")
else:
    from app.adapters.docx_form import *
