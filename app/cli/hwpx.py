"""HWPX(한글 XML 문서)를 읽고 표 칸에 글자를 써 넣는다.

HWPX는 zip이고 본문은 Contents/section*.xml에 있다. 표는 hp:tbl, 칸은 hp:tc이며
칸 위치는 hp:cellAddr(colAddr, rowAddr), 병합은 hp:cellSpan(colSpan, rowSpan)에 있다.
글자는 hp:p > hp:run > hp:t 에 있다.

    python tools/hwpx.py 양식.hwpx        # 표·칸 구조를 출력 (양식 분석용)
"""

import sys

from app.adapters.hwpx import dump


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for p in sys.argv[1:]:
        dump(p)
