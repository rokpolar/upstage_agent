"""텍스트가 들어 있는 견적서 PDF에서 정답 초안 JSON을 만든다 (사람이 검토할 초안).

    python tools/answers_from_text.py data_team/견적서예시 answers_team

- 업체명·대표자·견적일·품목(수량·단가·금액·단위)·공급가액·부가세를 표 텍스트에서 읽는다.
- 합계는 공급가액+부가세. 표지에 '합계금액 ...(VAT 별도)'(rules.json 패턴)로 적힌 값은 공급가액이므로 쓰지 않는다.
- 키 이름은 config/fields.json을 따른다. 비목·적요·비고는 사람이 판단할 값이라 넣지 않는다
  (score.py는 정답에 없는 키를 채점하지 않음).
- 표 산술이 맞지 않으면 review_flags에 사유를 넣는다.
- 스캔본(텍스트 없는 PDF)은 읽지 못하므로 '읽기 실패'로 표시하고 건너뛴다.
필요: pip install pypdf
"""

import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

from app.config import fields, find_pattern
from app.domain.money import check_amounts, to_int, won

NUM = r"\d{1,3}(?:,\d{3})*|\d+"
ROW = re.compile(
    rf"^\d+\s+(.+?)\s+(\S+)\s+({NUM})\s+({NUM})\s+({NUM})(?:\s+({NUM}))?\s*$"
)


def find_date(t):
    m = re.search(r"(\d{4})\s*[년.]\s*(\d{1,2})\s*[월.]\s*(\d{1,2})", t)
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else ""


def find_vendor(t):
    m = re.search(r"상\s*호\s+(.+?)\s+(?:성명|대\s*표\s*자)\s+(\S+)", t)
    if not m:
        m = re.search(r"공급자\s+(.+?)\s+대표\s+(\S+)", t)
    return (m[1].strip(), m[2].strip()) if m else ("", "")


def parse(text):
    items = []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        _, unit, a, b, c, d = m.groups()
        nums = [to_int(x) for x in (a, b, c, d) if x]
        # 세액 열이 있으면 숫자 4개(수량·단가·공급가액·세액), 없으면 3개
        if len(nums) == 4 and nums[0] * nums[1] == nums[2]:
            qty, price, amt = nums[:3]
        elif nums[-3] * nums[-2] == nums[-1]:
            qty, price, amt = nums[-3:]
            if len(nums) == 4:  # 마지막 세 숫자가 맞으면 첫 숫자는 규격에 붙은 숫자
                unit = str(a)
        else:
            continue
        I = fields()["item"]
        items.append(
            {
                I["quantity"]: qty,
                I["unit_price"]: price,
                I["amount"]: amt,
                I["basis"]: f"{won(price)}원*{won(qty)}{unit}",
            }
        )

    supply = vat = None
    m = re.search(rf"합\s*계\s+({NUM})\s+({NUM})\s*$", text, re.M)
    if m:
        supply, vat = to_int(m[1]), to_int(m[2])
    m = re.search(rf"공급가액\s*합계\s+({NUM})", text)
    if m:
        supply = to_int(m[1])
    m = re.search(rf"부가가치세[^\d\n]*?(?:\(10%\))?\s+({NUM})\s*$", text, re.M)
    if m:
        vat = to_int(m[1])
    vendor, rep = find_vendor(text)
    F = fields()
    ans = {
        F["vendor_name"]: vendor,
        F["vendor_representative"]: rep,
        F["quote_date"]: find_date(text),
        F["items"]: items,
        F["supply"]: supply,
        F["vat"]: vat,
        F["total"]: (supply + vat) if None not in (supply, vat) else None,
        F["flags"]: [],
    }
    check_amounts(ans)
    return ans


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    print(
        f"{'문서':<28}{'품목':>4}{'공급가액':>14}{'부가세':>12}{'합계':>14}  표지 VAT"
    )
    for p in sorted(src.glob("*.pdf")):
        text = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
        if len(text.strip()) < 50:
            print(f"{p.name:<28} 읽기 실패 (스캔본?)")
            continue
        ans = parse(text)
        F = fields()
        cover = "별도" if find_pattern("cover_total_vat_excluded", text) else "포함"
        (out / f"{p.stem}.json").write_text(
            json.dumps(ans, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        flag = f"  ⚠ {', '.join(ans[F['flags']])}" if ans[F["flags"]] else ""
        print(
            f"{p.stem[:26]:<28}{len(ans[F['items']]):>4}{won(ans[F['supply']] or 0):>14}"
            f"{won(ans[F['vat']] or 0):>12}{won(ans[F['total']] or 0):>14}  {cover}{flag}"
        )


if __name__ == "__main__":
    main()
