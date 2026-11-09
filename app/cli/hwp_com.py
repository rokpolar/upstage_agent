"""한글(HWP) 자동화로 문서 형식을 바꾼다. 어떤 양식 파일이 와도 HWPX로 맞춘 뒤 읽고 채우기 위해 쓴다.

    python tools/hwp_com.py 입력.hwp 출력.hwpx      # 확장자로 형식 결정 (config/rules.json runtime.hwp.formats)
    python tools/hwp_com.py 입력.hwpx 출력.pdf

한글이 설치된 Windows에서만 동작한다(pywin32 필요).
"""

import sys

from app.adapters.hwp_com import convert


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(convert(sys.argv[1], sys.argv[2]))
