"""HWPX를 한글 없이 HTML로 그려 미리보기·PDF를 만든다.

    python tools/hwpx_preview.py 문서.hwpx [--pdf]

양식은 고정이므로 미리보기도 양식 그대로 그린다:
  - 칸 너비: 표 전체의 칸 격자(colAddr, colSpan, cellSz)에서 열마다 너비를 풀어 <colgroup>으로 둔다.
  - 줄 높이, 칸 안 여백(cellMargin), 세로 정렬(subList vertAlign)
  - 문단 정렬(header.xml paraPr align), 글자 크기·굵기(charPr height, bold)
한글 자동화를 쓰지 않으므로 보안 확인창이 뜨지 않는다. 글꼴·선 굵기는 근사치다.
제출본은 .hwpx 원본이고, 이 미리보기는 확인·공유용이다.
"""

import argparse
import sys

from app.adapters.hwpx_preview import preview


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("hwpx")
    ap.add_argument("--pdf", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(preview(a.hwpx, a.pdf))
