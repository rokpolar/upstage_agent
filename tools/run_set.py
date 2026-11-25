"""구매 건(케이스) 단위 품의 판정: 입력 PDF들 → 판정 JSON + Word 품의서.

    python tools/run_set.py 입력폴더/*.pdf [--fresh] [--no-form]

입력은 몇 개든, 파일 하나에 견적서가 몇 장이든 된다. 파일 이름은 보지 않고 내용을 읽어 구매 건을 나눈다.
1) 파일을 쪽마다 나눈다(set_pipeline.split_dir).
2) 쪽마다 품의 워크플로를 동시에 돌린다. 이미 돌린 쪽 결과(set_pipeline.result_dir)가 있으면 다시 쓴다(--fresh면 새로).
3) 견적서가 아닌 쪽(분류가 견적서가 아니거나 품목·금액이 없는 쪽: FAX 송부장 등)은 뺀다.
4) 읽어 낸 견적서를 같은 물품끼리 묶는다(tools/grouping.py). 한 묶음 = 구매 건 1건 = 케이스 1건.
5) 케이스마다 판정(tools/set_decision.py)을 내려 out/sets/case_<A..>.json에 쓰고,
   모두 모아 정답지 템플릿 모양(task, unit, output_keys, decision_values, cases)으로 out/sets/answer_pred.json을 쓴다.
6) 작성 가능이면 Word 품의서(set_pipeline.form)를 채워 out/sets/case_<A..>_품의서.docx와 .pdf로 쓴다.
7) 정답지(set_pipeline.answer_file)가 있으면 케이스마다 output_keys를 맞춰 보고 다른 곳을 출력한다.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import ROOT, fields, rules
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from score import unwrap

from app.adapters.files import write_json
from app.adapters.n8n import N8nClient, workflow_client
from app.adapters.studio_errors import InputError, NotQuote, StudioError
from app.domain.procurement import cfg, check_quote, evaluate, selected_result


def split(pdf, dst, *, fresh=False, name=None):
    source = pdf.read_bytes()
    digest = hashlib.sha256(os.fsencode(pdf.resolve()) + b"\0")
    digest.update(source)
    folder = dst / digest.hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    pages = []
    for n, page in enumerate(PdfReader(io.BytesIO(source)).pages, 1):
        out = folder / f"page_{n}.pdf"
        if fresh or not out.is_file():
            writer = PdfWriter()
            writer.add_page(page)
            tmp = tempfile.NamedTemporaryFile(dir=folder, suffix=".pdf", delete=False)
            temporary = Path(tmp.name)
            try:
                with tmp:
                    writer.write(tmp)
                temporary.replace(out)
            finally:
                temporary.unlink(missing_ok=True)
        pages.append((name or pdf.name, n, out))
    if not pages:
        raise InputError("PDF에 읽을 쪽이 없습니다.")
    return pages


def rejected(name, page, category, *, error=None):
    row = {"file": name, "page": page, "quote": None, "category": category}
    if error is not None:
        print(f"  {name} {page}쪽 · {error}", file=sys.stderr)
        row["error"] = error
    return row


def read_page(page, res_dir, fresh, client):
    name, n, path = page
    res = res_dir / path.parent.name / f"{path.stem}.json"
    if not fresh and res.is_file():
        try:
            result = unwrap(json.loads(res.read_text(encoding="utf-8")))
            check_quote(result)
        except (OSError, ValueError):
            result = None
        if result is not None:
            return quote_row(name, n, result)
    # Invalidate the previous result before rerunning so rejected pages cannot be reused.
    res.unlink(missing_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="quote-") as pending:
            raw, _ = client.run_file(path, pending)
        result = unwrap(raw)
        check_quote(result)
    except NotQuote as exc:
        return rejected(name, n, exc.category)
    except StudioError as exc:
        return rejected(name, n, "처리 실패", error=f"Studio 오류({exc.kind})")
    except (ValueError, OSError) as exc:
        return rejected(
            name, n, "처리 실패", error=str(exc) or "견적을 읽지 못했습니다."
        )
    row = quote_row(name, n, result)
    if row["quote"] is not None:
        write_json(res, result)
    return row


def quote_row(name, page, result):
    f = fields()
    if not result.get(f["items"]) and result.get(f["total"]) is None:
        return rejected(name, page, (result.get("studio") or {}).get("category") or "-")
    return {"file": name, "page": page, "quote": result, "category": None}


def write_form(case, result, out, out_dir):
    from docx_pdf import to_pdf
    from fill_form import fill

    selection = {
        "quote_count": out["quote_count"],
        "vendor": out["selected_vendor"],
        "reason": out["reason"],
    }
    doc, report = fill(
        ROOT / cfg()["form"],
        result,
        {"case_id": case, "draft_date": dt.date.today().isoformat()},
        selection,
    )
    dst = doc.save(out_dir / f"{case}_품의서.docx")
    pdf, way = to_pdf(dst)
    return dst, pdf, way, report


def read_all(pdfs, fresh, client):
    """Split and read documents while keeping errors attached to their inputs."""
    c = cfg()
    split_dir, res_dir = ROOT / c["split_dir"], ROOT / c["result_dir"]
    if isinstance(client, N8nClient):
        res_dir = res_dir / "n8n"
    pdfs = list(dict.fromkeys(pdfs))
    names = Counter(pdf.name for pdf in pdfs)
    pages, failures = [], []
    for pdf in pdfs:
        name = pdf.name if names[pdf.name] == 1 else str(pdf)
        try:
            pages.extend(split(pdf, split_dir, fresh=fresh, name=name))
        except (InputError, OSError, PyPdfError):
            failures.append(
                rejected(name, 1, "처리 실패", error="PDF를 읽을 수 없습니다.")
            )
    with ThreadPoolExecutor(
        max_workers=rules()["runtime"]["studio"].get("workers", 4)
    ) as pool:
        return (
            list(pool.map(lambda p: read_page(p, res_dir, fresh, client), pages))
            + failures
        )


def run_case(case, evaluated, form=True):
    """Write the case and, when allowed, fill the selected quote into the form."""
    c = cfg()
    out_dir = ROOT / c["out_dir"]
    out, reads = evaluated
    raw = (
        selected_result(out, reads)
        if form and out["decision"] == c["decisions"]["ok"]
        else None
    )
    skipped = out.get("not_quote_pages", [])
    files = sorted({name for name, *_ in reads})
    write_json(out_dir / f"{case}.json", out)
    print(
        f"{case} (파일 {len(files)}개, 견적서 {out['quote_count']}장): {out['decision']} | {out['selected_vendor'] or '-'} "
        f"{out['selected_total'] or ''} | {out['reason']}"
    )
    print(f"  파일: {', '.join(files)}")
    if skipped:
        print(f"  견적서 아님: {skipped}")
    if raw is not None:
        dst, pdf, way, report = write_form(case, raw, out, out_dir)
        print(
            f"  품의서: {dst.relative_to(ROOT)}, {pdf.relative_to(ROOT)} (PDF: {way}, 빈칸 {len(report['missing'])}개)"
        )
    return out


def as_answer(outs):
    """케이스 결과들을 정답지 템플릿 모양으로."""
    c = cfg()
    keys = c["output_keys"]
    return {
        "task": c["task"],
        "unit": c["unit"],
        "output_keys": keys,
        "decision_values": list(c["decisions"].values()),
        "cases": [
            {
                "case_id": chr(ord("A") + i),
                "file": o["file"],
                "documents": o["documents"],
                "answer": {k: o.get(k) for k in keys},
            }
            for i, o in enumerate(outs)
        ],
    }


def compare(pred, ans):
    by_file = {json.dumps(x["file"], ensure_ascii=False): x for x in ans["cases"]}
    for p in pred["cases"]:
        a = by_file.get(json.dumps(p["file"], ensure_ascii=False))
        if a is None:
            print(f"  정답지에 없음: {p['file']}")
            continue
        diffs = [
            f"{k}: 결과 {p['answer'].get(k)!r} / 정답 {a['answer'].get(k)!r}"
            for k in pred["output_keys"]
            if p["answer"].get(k) != a["answer"].get(k)
        ]
        print(f"  {p['file']}: {'정답과 일치' if not diffs else ' · '.join(diffs)}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pdf", nargs="+")
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="이미 돌린 쪽 결과가 있어도 워크플로를 다시 실행한다",
    )
    ap.add_argument(
        "--no-form", action="store_true", help="Word 품의서를 만들지 않는다"
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    client = workflow_client()
    try:
        rows = read_all(sorted(Path(x).resolve() for x in a.pdf), a.fresh, client)
        result = (
            client.purchase(rows) if isinstance(client, N8nClient) else evaluate(rows)
        )
        failed_files = {page["file"] for page in result["failed_pages"]}
        non_quote_files = {row[0] for row in result["loose"]} - failed_files
        if non_quote_files:
            print(f"견적서가 없는 파일: {sorted(non_quote_files)}")
        outs = [
            run_case(
                f"case_{chr(ord('A') + i)}",
                (case["result"], case["rows"]),
                not a.no_form,
            )
            for i, case in enumerate(result["cases"])
        ]
    except ValueError as exc:
        sys.exit(str(exc))
    pred = as_answer(outs)
    out_dir = ROOT / cfg()["out_dir"]
    write_json(out_dir / "answer_pred.json", pred)
    ans = ROOT / cfg()["answer_file"]
    if ans.exists():
        print("정답지 대조:")
        compare(pred, json.loads(ans.read_text(encoding="utf-8")))
    if result["failed_pages"]:
        sys.exit(
            f"처리 실패 {len(result['failed_pages'])}쪽. 파일을 확인한 뒤 다시 실행하세요."
        )


if __name__ == "__main__":
    main()
