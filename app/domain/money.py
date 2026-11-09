import re

from app.config import fields, rules


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
                d = chunk // (10**i) % 10
                if d:
                    s += digits[d] + small[i]
            out.append(s + big[group])
        n //= 10000
        group += 1
    return "".join(reversed(out))
