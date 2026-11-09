"""tools 스크립트가 같이 쓰는 설정 읽기, 금액 계산·표기, PDF 변환.

규칙·키 이름·문구는 코드에 두지 않고 config/ 에서 읽는다.
  config/rules.json       부가세율, 허용 오차, 원문 패턴, 품의서 기본값, 실행 환경
  config/fields.json      역할 → 실제 키 이름 (Studio 스키마와 같은 키)
  config/questions.json   담당자 질문 문구
  config/categories.json  비목 허용 목록
"""
import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


@lru_cache(maxsize=None)
def config(name):
    return json.loads((CONFIG / f"{name}.json").read_text(encoding="utf-8"))


def rules():
    return config("rules")


def fields():
    return config("fields")


def categories():
    return config("categories")["categories"]


def won(n):
    return f"{n:,}"


def to_int(v):
    if isinstance(v, (int, float)):
        return int(round(v))
    if isinstance(v, str):
        s = re.sub(r"[^\d\-]", "", v)
        return int(s) if s not in ("", "-") else None
    return None


def vat_of(supply):
    """규칙의 부가세율로 계산한 부가세(원 단위 절사)."""
    return int(supply * rules()["vat_rate"])


def find_pattern(name, text):
    """rules.json patterns[name]의 정규식 중 처음 맞는 것의 match (없으면 None)."""
    for p in rules()["patterns"][name]:
        m = re.search(p, text)
        if m:
            return m
    return None


def check_amounts(r):
    """추출 결과의 원 단위 산술을 코드로 검사해 검토 사유 목록에 더한다(정답 초안 생성용).

    r을 그대로 고치고, 새로 붙인 사유 목록을 돌려준다. 문구는 questions.json의 제목을 쓴다.
    """
    F, I = fields(), fields()["item"]
    tol = rules()["amount_tolerance_won"]
    flags = r.setdefault(F["flags"], [])
    added = []

    def add(key):
        if key not in flags:
            flags.append(key)
            added.append(key)

    items = r.get(F["items"]) or []
    for it in items:
        q, p, a = (to_int(it.get(I[k])) for k in ("quantity", "unit_price", "amount"))
        if None not in (q, p, a) and abs(q * p - a) > tol:
            add("item_calc")
    supply, vat, total = (to_int(r.get(F[k])) for k in ("supply", "vat", "total"))
    item_sum = sum(to_int(it.get(I["amount"])) or 0 for it in items)
    if items and supply is not None and abs(item_sum - supply) > tol:
        add("supply_sum")
    if None not in (supply, vat, total) and abs(supply + vat - total) > tol:
        add("total_sum")
    if None in (supply, total):
        add("missing_amount")
    return added


def html_to_pdf(html_path, pdf_path):
    candidates = [os.environ.get("PDF_BROWSER")] + rules()["runtime"]["pdf_browsers"]
    browser = next((b for b in candidates if b and Path(b).exists()), None)
    if browser is None:
        sys.exit("PDF 변환에 쓸 Edge/Chrome을 찾지 못했습니다. 환경변수 PDF_BROWSER나 config/rules.json runtime.pdf_browsers에 경로를 넣어 주세요.")
    subprocess.run(
        # PDF_BROWSER_FLAGS: 컨테이너(root 실행)처럼 추가 옵션이 필요할 때만 쓴다(예: --no-sandbox)
        [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", *os.environ.get("PDF_BROWSER_FLAGS", "").split(),
         f"--print-to-pdf={Path(pdf_path).resolve()}", Path(html_path).resolve().as_uri()],
        check=True, capture_output=True, timeout=120,
    )


def korean_amount(n):
    """1347500 -> '일백삼십사만칠천오백' (금액 한글 표기, '원정'은 호출부에서 붙인다)."""
    digits = "영일이삼사오육칠팔구"
    small = ["", "십", "백", "천"]
    big = ["", "만", "억", "조"]
    if n == 0:
        return "영"
    out = []
    group = 0
    while n > 0:
        chunk = n % 10000
        if chunk:
            s = ""
            for i in range(3, -1, -1):
                d = chunk // (10 ** i) % 10
                if d:
                    s += digits[d] + small[i]
            out.append(s + big[group])
        n //= 10000
        group += 1
    return "".join(reversed(out))
