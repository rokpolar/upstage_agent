"""이전 도구 import와의 호환성."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.adapters.pdf import html_to_pdf
from app.config import *
from app.domain.money import *
