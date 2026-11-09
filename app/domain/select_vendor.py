"""견적서가 여러 개일 때 비교해 업체를 추천하고, 담당자가 고르거나 직접 사유를 적게 한다.

    python tools/select_vendor.py results/a.json results/b.json results/c.json [--source a.pdf b.pdf c.pdf] [--case 구매건.json]
    python tools/select_vendor.py ... --no-input        # 추천만 저장(웹·n8n이 화면에 띄울 용도)
    python tools/select_vendor.py ... --pick 2 --reason "납기 준수 실적"   # 화면에서 고른 결과를 저장

- 각 견적을 하네스(harness.review)로 검사해 문제(유효기간 경과 등)를 함께 보여준다.
- 추천 규칙(제외 조건·정렬 순서)과 사유 문구는 config/rules.json vendor_selection,
  화면 문구는 config/questions.json vendor_select에 있다.
- 추천 업체를 고르면 자동 사유를 쓰고, 다른 업체를 고르면 사유를 꼭 입력받는다(감사 대비).

출력: <out>/selection.json  {vendor, result_file, reason, recommended, by_user, quote_count, comparison[]}
fill_form.py --selection 으로 넘기면 선정 업체·선정 사유가 품의서에 들어간다.
"""

import datetime as dt

from app.config import config, rules
from app.domain.money import won


def rank(quotes):
    cfg = rules()["vendor_selection"]
    keys = {
        "total_asc": lambda q: q["total"],
        "total_desc": lambda q: -q["total"],
        "quote_date_desc": lambda q: -int(q["quote_date"].replace("-", "") or 0),
        "quote_date_asc": lambda q: int(q["quote_date"].replace("-", "") or 0),
        "issues_asc": lambda q: len(q["issues"]),
    }
    eligible = [q for q in quotes if not set(q["issues"]) & set(cfg["exclude_if"])]
    order = sorted(eligible, key=lambda q: tuple(keys[k](q) for k in cfg["rank_by"]))
    return order, [q for q in quotes if q not in eligible]


def auto_reason(order, quotes):
    cfg = rules()["vendor_selection"]
    parts, best = cfg["reason_parts"], order[0]
    if len(quotes) == 1:
        text = [parts["single"].format(vendor=best["vendor"], total=won(best["total"]))]
    else:
        text = [parts["lowest"].format(count=len(quotes), total=won(best["total"]))]
        if len(order) > 1:
            second = order[1]
            text.append(
                parts["gap"].format(
                    second_vendor=second["vendor"],
                    second_total=won(second["total"]),
                    gap=won(second["total"] - best["total"]),
                )
            )
    if "expired" not in best["issues"]:
        text.append(parts["valid"])
    return cfg["reason_joiner"].join(text)


def recommend(quotes):
    """(추천 견적 또는 None, 자동 사유, 정렬된 후보, 제외된 견적)."""
    order, excluded = rank(quotes)
    rec = order[0] if order else None
    return rec, (auto_reason(order, quotes) if rec else ""), order, excluded


def finalize(quotes, rec, auto, chosen, reason, by_user):
    """고른 견적과 사유로 selection을 만든다. 추천이 아닌 업체는 사유가 있어야 한다(없으면 ValueError)."""
    if chosen is rec:
        reason = reason or auto
    elif not reason:
        raise ValueError(config("questions")["vendor_select"]["reason_missing"])
    return {
        "vendor": chosen["vendor"],
        "result_file": chosen["file"],
        "reason": reason,
        "recommended": rec["vendor"] if rec else None,
        "auto_reason": auto,
        "by_user": by_user,
        "quote_count": len(quotes),
        "chosen_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "comparison": [
            {k: q[k] for k in ("vendor", "total", "quote_date", "issues", "file")}
            for q in quotes
        ],
    }
