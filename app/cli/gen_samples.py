"""품의 agent 개발용 가상 견적서 PDF와 정답 JSON을 함께 만든다.

견적서 하나의 데이터(SAMPLES)에서 HTML 견적서와 정답 JSON을 같이 뽑아
둘이 어긋나지 않게 한다. PDF 변환은 Edge(또는 Chrome) headless를 쓴다.

    python tools/gen_samples.py
"""

import html
import json
import shutil

from app.adapters.pdf import html_to_pdf
from app.config import ROOT, fields
from app.domain.money import korean_amount, vat_of, won

OUT_PDF = ROOT / "samples"
OUT_ANS = ROOT / "answers"
TMP = ROOT / "samples" / "_html"

# 아래 SAMPLES는 시험용 가상 견적서 '데이터'다(업체·품목·금액). 정답 JSON의 키 이름은
# config/fields.json, 부가세율은 config/rules.json에서 읽는다.
# 정답 규칙
# - 금액은 견적서에 적힌 값을 그대로 쓴다(틀린 합계도 그대로 두고 검토 사유를 넣는다).
# - 품목 금액은 공급가, 합계는 부가세 포함.
# - 부가세 표기가 없으면 부가세 0, 검토 사유를 넣는다.
SAMPLES = [
    {
        "id": "q01",
        "type": "단순",
        "layout": "standard",
        "vendor_name": "㈜진주인쇄",
        "vendor_representative": "이인쇄",
        "biz_no": "123-45-67890",
        "address": "대구광역시 북구 가상로 12",
        "quote_date": "2026-11-02",
        "items": [
            {
                "name": "포스터 제작",
                "spec": "A2, 아트지 150g",
                "qty": 100,
                "unit": "장",
                "unit_price": 4000,
                "category": "홍보비",
                "description": "사업 홍보를 위한 포스터 제작(A2, 아트지 150g)",
            },
            {
                "name": "현수막 제작",
                "spec": "5m x 0.9m",
                "qty": 10,
                "unit": "개",
                "unit_price": 50000,
                "category": "홍보비",
                "description": "사업 홍보를 위한 현수막 제작(5m x 0.9m)",
            },
        ],
        "flags": [],
    },
    {
        "id": "q02",
        "type": "양식·문맥 다양성",
        "layout": "per_line_tax",
        "vendor_name": "오피스플러스 주식회사",
        "vendor_representative": "김사무",
        "biz_no": "234-56-78901",
        "address": "경상북도 경산시 가상길 34",
        "quote_date": "2026-10-28",
        "items": [
            {
                "name": "A4 복사용지",
                "spec": "80g, 2500매/박스",
                "qty": 5,
                "unit": "박스",
                "unit_price": 28000,
                "category": "사무용품비",
                "description": "사무용 A4 복사용지 구입(80g, 2500매/박스)",
            },
            {
                "name": "볼펜",
                "spec": "0.7mm, 12입",
                "qty": 10,
                "unit": "갑",
                "unit_price": 6000,
                "category": "사무용품비",
                "description": "사무용 볼펜 구입(0.7mm, 12입)",
            },
            {
                "name": "토너 카트리지",
                "spec": "TN-2480",
                "qty": 2,
                "unit": "개",
                "unit_price": 85000,
                "category": "사무용품비",
                "description": "프린터 토너 카트리지 구입(TN-2480)",
            },
        ],
        "flags": [],
    },
    {
        "id": "q03",
        "type": "예외·함정",
        "layout": "standard",
        "vendor_name": "㈜ab미디어",
        "vendor_representative": "박기획",
        "biz_no": "345-67-89012",
        "address": "울산광역시 남구 가상대로 56",
        "quote_date": "2026-11-03",
        "items": [
            {
                "name": "행사 사진 촬영",
                "spec": "4시간, 보정 포함",
                "qty": 1,
                "unit": "식",
                "unit_price": 300000,
                "category": "용역비",
                "description": "행사 기록을 위한 사진 촬영 용역(4시간, 보정 포함)",
            },
            {
                "name": "행사 영상 편집",
                "spec": "3분 하이라이트",
                "qty": 1,
                "unit": "식",
                "unit_price": 250000,
                "category": "용역비",
                "description": "행사 홍보용 하이라이트 영상 편집 용역(3분)",
            },
        ],
        # 품목 합계는 550,000원인데 견적서에는 공급가액을 600,000원으로 잘못 적었다.
        "stated_supply": 600000,
        "flags": ["품목 금액 합계와 공급가액 불일치"],
    },
    {
        "id": "q04",
        "type": "예외·함정",
        "layout": "simple_no_vat",
        "vendor_name": "대구렌탈",
        "vendor_representative": "최대여",
        "biz_no": "456-78-90123",
        "address": "대구광역시 동구 가상동 78",
        "quote_date": "2026-11-01",
        "items": [
            {
                "name": "빔프로젝터 대여",
                "spec": "5000안시, 1일",
                "qty": 2,
                "unit": "대",
                "unit_price": 70000,
                "category": "임차료",
                "description": "행사 발표용 빔프로젝터 대여(1일)",
            },
            {
                "name": "스크린 대여",
                "spec": "100인치, 1일",
                "qty": 2,
                "unit": "대",
                "unit_price": 30000,
                "category": "임차료",
                "description": "행사 발표용 스크린 대여(100인치, 1일)",
            },
        ],
        "no_vat": True,
        "flags": ["부가세 표기 없음"],
    },
    {
        "id": "q05",
        "type": "양식·문맥 다양성",
        "layout": "total_on_top",
        "vendor_name": "테크마트㈜",
        "vendor_representative": "정기기",
        "biz_no": "567-89-01234",
        "address": "경상북도 구미시 가상산단로 90",
        "quote_date": "2026-10-30",
        "items": [
            {
                "name": "노트북",
                "spec": "15.6형, i5, 16GB, 512GB",
                "qty": 1,
                "unit": "대",
                "unit_price": 1200000,
                "category": "장비구입비",
                "description": "업무용 노트북 구입(15.6형, i5, 16GB, 512GB)",
            },
            {
                "name": "무선 마우스",
                "spec": "2.4GHz",
                "qty": 1,
                "unit": "개",
                "unit_price": 25000,
                "category": "장비구입비",
                "description": "업무용 무선 마우스 구입",
            },
        ],
        "flags": [],
    },
]


def totals(s):
    item_sum = sum(it["qty"] * it["unit_price"] for it in s["items"])
    supply = s.get("stated_supply", item_sum)
    vat = 0 if s.get("no_vat") else vat_of(supply)
    return item_sum, supply, vat, supply + vat


def answer(s):
    _, supply, vat, total = totals(s)
    F, I = fields(), fields()["item"]
    return {
        F["vendor_name"]: s["vendor_name"],
        F["vendor_representative"]: s["vendor_representative"],
        F["quote_date"]: s["quote_date"],
        F["items"]: [
            {
                I["category"]: it["category"],
                I["description"]: it["description"],
                I["quantity"]: it["qty"],
                I["unit_price"]: it["unit_price"],
                I["amount"]: it["qty"] * it["unit_price"],
                I["basis"]: f"{won(it['unit_price'])}원*{won(it['qty'])}{it['unit']}",
                I["note"]: "",
            }
            for it in s["items"]
        ],
        F["supply"]: supply,
        F["vat"]: vat,
        F["total"]: total,
        F["flags"]: s["flags"],
    }


CSS = """
@page { size: A4; margin: 18mm; }
body { font-family: 'Malgun Gothic', sans-serif; font-size: 11pt; color: #111; }
h1 { text-align: center; letter-spacing: 1.2em; font-size: 22pt; margin: 0 0 18px; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #333; padding: 6px 8px; }
th { background: #f0f0f0; font-weight: 600; }
.num { text-align: right; }
.c { text-align: center; }
.noborder td { border: none; padding: 2px 0; }
.big { font-size: 15pt; font-weight: 700; }
.foot { margin-top: 24px; font-size: 9pt; color: #666; }
"""


def esc(x):
    return html.escape(str(x))


def supplier_box(s):
    return f"""
<table style="width:55%;float:right;margin-bottom:14px">
<tr><th rowspan="4" style="width:22px">공<br>급<br>자</th><th>상호</th><td>{esc(s["vendor_name"])}</td></tr>
<tr><th>대표자</th><td>{esc(s["vendor_representative"])} (인)</td></tr>
<tr><th>사업자번호</th><td>{esc(s["biz_no"])}</td></tr>
<tr><th>주소</th><td>{esc(s["address"])}</td></tr>
</table>"""


def item_rows(s, with_tax=False):
    rows = []
    for i, it in enumerate(s["items"], 1):
        amt = it["qty"] * it["unit_price"]
        tax = f'<td class="num">{won(vat_of(amt))}</td>' if with_tax else ""
        rows.append(
            f'<tr><td class="c">{i}</td><td>{esc(it["name"])}</td><td>{esc(it["spec"])}</td>'
            f'<td class="c">{it["qty"]}</td><td class="c">{esc(it["unit"])}</td>'
            f'<td class="num">{won(it["unit_price"])}</td><td class="num">{won(amt)}</td>{tax}</tr>'
        )
    return "\n".join(rows)


def render(s):
    _, supply, vat, total = totals(s)
    date_dot = s["quote_date"].replace("-", ".")
    layout = s["layout"]
    if layout == "standard":
        body = f"""
<h1>견적서</h1>
<table class="noborder" style="width:42%;float:left"><tr><td>견적일자: {esc(s["quote_date"])}</td></tr>
<tr><td>수신: 귀하</td></tr><tr><td>아래와 같이 견적합니다.</td></tr></table>
{supplier_box(s)}
<div style="clear:both"></div>
<table><tr><th>No</th><th>품명</th><th>규격</th><th>수량</th><th>단위</th><th>단가</th><th>금액</th></tr>
{item_rows(s)}
<tr><th colspan="6">공급가액</th><td class="num">{won(supply)}</td></tr>
<tr><th colspan="6">부가세(10%)</th><td class="num">{won(vat)}</td></tr>
<tr><th colspan="6">합계</th><td class="num big">{won(total)}</td></tr></table>
<p>견적 유효기간: 견적일로부터 30일</p>"""
    elif layout == "per_line_tax":
        body = f"""
<h1>견 적 서</h1>
<p><b>{esc(s["vendor_name"])}</b> | 대표 {esc(s["vendor_representative"])} | 사업자등록번호 {esc(s["biz_no"])}<br>
{esc(s["address"])}</p>
<p>견적번호 OP-2610-0412 &nbsp;&nbsp; 작성일 {esc(date_dot)}</p>
<table><tr><th>순번</th><th>품목</th><th>규격</th><th>수량</th><th>단위</th><th>단가</th><th>공급가</th><th>세액</th></tr>
{item_rows(s, with_tax=True)}
<tr><th colspan="6">소계</th><td class="num">{won(supply)}</td><td class="num">{won(vat)}</td></tr>
<tr><th colspan="6">총 견적금액 (VAT 포함)</th><td class="num big" colspan="2">{won(total)}원</td></tr></table>
<p>납기: 발주 후 3일 이내 · 결제조건: 납품 후 계좌이체</p>"""
    elif layout == "simple_no_vat":
        lines = "".join(
            f"<li>{esc(it['name'])} ({esc(it['spec'])}) — {it['qty']}{esc(it['unit'])} × {won(it['unit_price'])}원 = {won(it['qty'] * it['unit_price'])}원</li>"
            for it in s["items"]
        )
        body = f"""
<h1 style="letter-spacing:.3em">간이 견적</h1>
<p>{esc(date_dot)}</p>
<ul style="line-height:2">{lines}</ul>
<p class="big">합계 {won(total)}원</p>
<p>{esc(s["vendor_name"])} 대표 {esc(s["vendor_representative"])} · {esc(s["biz_no"])} · {esc(s["address"])}</p>"""
    elif layout == "total_on_top":
        body = f"""
<h1>견적서</h1>
<table><tr><th style="width:28%">견적금액</th><td class="big">일금 {korean_amount(total)}원정 (₩{won(total)})</td></tr>
<tr><th>공급가액 / 세액</th><td>{won(supply)}원 / {won(vat)}원</td></tr>
<tr><th>견적일</th><td>{esc(s["quote_date"])}</td></tr></table>
<br>{supplier_box(s)}<div style="clear:both"></div>
<table><tr><th>No</th><th>품명</th><th>사양</th><th>수량</th><th>단위</th><th>단가</th><th>금액</th></tr>
{item_rows(s)}</table>
<p>배송: 무료 · A/S: 1년</p>"""
    else:
        raise ValueError(layout)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>{body}<p class="foot">가상 데이터 · 해커톤 시연용 (실제 업체와 무관)</p></body></html>"""


def main():
    for d in (OUT_PDF, OUT_ANS, TMP):
        d.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in SAMPLES:
        page = TMP / f"{s['id']}.html"
        page.write_text(render(s), encoding="utf-8")
        html_to_pdf(page, OUT_PDF / f"{s['id']}.pdf")
        (OUT_ANS / f"{s['id']}.json").write_text(
            json.dumps(answer(s), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rows.append((s["id"], s["type"], s["layout"], ", ".join(s["flags"]) or "-"))
    shutil.rmtree(TMP)
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    print("id   유형              양식            검토 필요")
    for r in rows:
        print(f"{r[0]}  {r[1]:<16} {r[2]:<15} {r[3]}")


if __name__ == "__main__":
    main()
