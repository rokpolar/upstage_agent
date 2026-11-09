"""견적서 여러 장이 든 PDF 한 파일 → 품의 판정 JSON(<입력파일명>.json) + Word 품의서.

    python tools/run_set.py 팀명_최종제출_품의_set01.pdf [다른 세트.pdf ...] [--fresh] [--no-form]

1) PDF를 쪽마다 나눈다(set_pipeline.split_dir).
2) 쪽마다 Studio 품의 워크플로를 동시에 돌린다. 이미 돌린 쪽 결과(set_pipeline.result_dir)가 있으면 다시 쓴다(--fresh면 새로).
3) 견적서가 아닌 쪽(분류가 견적서가 아니거나 품목·금액이 없는 쪽: FAX 송부장 등)은 뺀다.
4) 세트 판정(tools/set_decision.py)을 내려 out/sets/<입력파일명>.json에 쓴다. 키는 정답지와 같다.
5) 작성 가능이면 Word 품의서(set_pipeline.form)를 채워 out/sets/<입력파일명>_품의서.docx로 쓴다.
6) 정답(answers_final/<입력파일명>.json)이 있으면 맞춰 보고 다른 곳을 출력한다.
"""
import argparse
import datetime as dt
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import ROOT, fields, rules
from run_api import NotQuote, StudioError, run_file
from score import unwrap
from set_decision import cfg, decide

COMPARE_TOP = ["decision", "selected_vendor", "selected_total", "quote_count"]
COMPARE_QUOTE = ["vendor_name", "vendor_representative", "quote_date", "supply_amount", "vat_amount", "total_amount"]


def split(pdf, dst):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(pdf))
    pages = []
    for n, page in enumerate(reader.pages, 1):
        out = dst / f"{pdf.stem}_p{n}.pdf"
        if not out.exists():
            w = PdfWriter()
            w.add_page(page)
            buf = io.BytesIO()
            w.write(buf)
            out.write_bytes(buf.getvalue())
        pages.append((n, out))
    return pages


def read_page(n, path, res_dir, fresh):
    res = res_dir / f"{path.stem}.json"
    if fresh or not res.exists():
        try:
            run_file(path, res_dir)
        except NotQuote as e:
            return n, None, e.category
        except StudioError as e:  # 한 쪽이 실패해도 세트는 계속 본다. 실패한 쪽은 결과 JSON의 not_quote_pages에 남는다
            print(f"  p{n} Studio 오류({e.kind}): {e}", file=sys.stderr)
            return n, None, f"Studio 오류: {e.kind}"
    r = unwrap(json.loads(res.read_text(encoding="utf-8")))
    F = fields()
    if not (r.get(F["items"]) or r.get(F["total"])):
        return n, None, (r.get("studio") or {}).get("category") or "-"
    return n, r, None


def to_quote(n, r):
    F, keys = fields(), cfg()["item_keys"]
    return {"page": n, "vendor_name": r.get(F["vendor_name"]), "vendor_representative": r.get(F["vendor_representative"]) or "",
            "quote_date": r.get("quote_date") or "",
            "items": [{k: it.get(k) for k in keys} for it in r.get(F["items"]) or []],
            "supply_amount": r.get("supply_amount"), "vat_amount": r.get("vat_amount"), "total_amount": r.get(F["total"]),
            "review_flags": list(r.get("review_flags") or [])}


def write_form(stem, result, out, out_dir):
    from docx_preview import preview
    from fill_form import fill
    case = {"case_id": stem, "draft_date": dt.date.today().isoformat()}
    selection = {"quote_count": out["quote_count"], "vendor": out["selected_vendor"], "reason": out["reason"]}
    doc, report = fill(ROOT / cfg()["form"], result, case, selection)
    dst = doc.save(out_dir / f"{stem}_품의서.docx")
    preview(dst)
    return dst, report


def compare(out, ans):
    diffs = [f"{k}: 결과 {out.get(k)!r} / 정답 {ans.get(k)!r}" for k in COMPARE_TOP if out.get(k) != ans.get(k)]
    mine = {q["page"]: q for q in out["quotes"]}
    for a in ans["quotes"]:
        q = mine.get(a["page"])
        if q is None:
            diffs.append(f"p{a['page']} {a['vendor_name']}: 결과에 없음")
            continue
        diffs += [f"p{a['page']} {k}: 결과 {q.get(k)!r} / 정답 {a.get(k)!r}" for k in COMPARE_QUOTE if q.get(k) != a.get(k)]
        if len(q["items"]) != len(a["items"]):
            diffs.append(f"p{a['page']} 품목 수: 결과 {len(q['items'])} / 정답 {len(a['items'])}")
        if bool(q["review_flags"]) != bool(a["review_flags"]):
            diffs.append(f"p{a['page']} 확인 사항: 결과 {q['review_flags']} / 정답 {a['review_flags']}")
    return diffs


def run(pdf, fresh=False, form=True):
    c = cfg()
    split_dir, res_dir, out_dir = (ROOT / c[k] for k in ("split_dir", "result_dir", "out_dir"))
    for d in (split_dir, res_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)
    pages = split(pdf, split_dir)
    workers = rules()["runtime"]["studio"].get("workers", 4)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        read = sorted(ex.map(lambda p: read_page(p[0], p[1], res_dir, fresh), pages))
    quotes = [to_quote(n, r) for n, r, _ in read if r is not None]
    skipped = [{"page": n, "category": cat} for n, r, cat in read if r is None]
    out = decide(pdf.name, quotes, skipped)
    (out_dir / f"{pdf.stem}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{pdf.name}: {out['decision']} | {out['selected_vendor'] or '-'} {out['selected_total'] or ''} | {out['reason']}")
    if skipped:
        print(f"  견적서 아님: {skipped}")
    if form and out["decision"] == c["decisions"]["ok"]:  # 품의서는 작성 가능일 때만
        result = next(r for n, r, _ in read if r is not None and r.get(fields()["vendor_name"]) == out["selected_vendor"])
        dst, report = write_form(pdf.stem, result, out, out_dir)
        print(f"  품의서: {dst.relative_to(ROOT)} (빈칸 {len(report['missing'])}개)")
    ans = ROOT / c["answer_dir"] / f"{pdf.stem}.json"
    if ans.exists():
        diffs = compare(out, json.loads(ans.read_text(encoding="utf-8")))
        print(f"  정답 대조: {'모두 일치' if not diffs else f'{len(diffs)}곳 다름'}")
        for x in diffs:
            print(f"    - {x}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", nargs="+")
    ap.add_argument("--fresh", action="store_true", help="이미 돌린 쪽 결과가 있어도 Studio를 다시 돌린다")
    ap.add_argument("--no-form", action="store_true", help="Word 품의서를 만들지 않는다")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    for p in a.pdf:
        run(Path(p).resolve(), a.fresh, not a.no_form)


if __name__ == "__main__":
    main()
