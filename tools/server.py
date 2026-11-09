"""품의 agent 로컬 웹 화면 서버 (표준 라이브러리만 사용).

    python tools/server.py          # http://127.0.0.1:8765 (config/rules.json ui.host/port)
    python tools/server.py --open   # 켜면서 브라우저도 연다

화면(ui/)이 부르는 API로 기존 도구를 그대로 쓴다:
  견적 검사·질문 = harness.review/resolve, 업체 추천 = select_vendor.recommend/finalize,
  양식 채우기 = fill_form.fill, 미리보기 = hwpx_preview.
상태(답변·선정)는 서버 메모리에 둔다. 다시 켜면 처음부터.
"""
import base64
import datetime as dt
import json
import mimetypes
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from common import ROOT, config, fields, rules
from fill_form import fill
from grouping import group as group_quotes
from harness import ACK, HOLD, coerce, normalize, recheck, resolve, source_text
from hwpx_preview import preview
from score import unwrap
from select_vendor import cross_checks, finalize, recommend

UI = ROOT / "ui"
CFG = rules()["ui"]
OUT = ROOT / CFG["output_dir"]
STATE = {"case": {}, "quotes": {}, "selection": None, "texts": {}}


# ---------------------------------------------------------------- 견적

def add_quote(n, p, src_dir):
    src = next((s for s in (ROOT / src_dir).glob(f"{p.stem}.*") if s.suffix.lower() == ".pdf"), None)
    data = json.loads(p.read_text(encoding="utf-8"))
    qid = f"{n}:{p.stem}"
    STATE["quotes"][qid] = {
        "set": n, "name": p.stem, "file": str(p), "source": str(src) if src else None,
        "state": {"result": normalize(unwrap(data) or data), "questions": [], "resolution_log": [],
                  "acknowledged": [], "hold": False, "source": str(src) if src else None}}
    check(qid)
    return qid


def load_quotes():
    STATE["quotes"].clear()
    for n, (res_dir, src_dir, _label) in enumerate(CFG["quote_sets"]):
        for p in sorted((ROOT / res_dir).glob("*.json")):
            add_quote(n, p, src_dir)


def studio_missing():
    from run_api import settings
    return settings()[1]


class NotQuoteError(ValueError):
    def __init__(self, message, category):
        super().__init__(message)
        self.category = category


PROGRESS = {}  # 올리는 중인 견적서: token → {"done": [...], "found": {...}, "finished": bool}


def split_pages(name, data_b64):
    """여러 쪽 PDF를 쪽마다 한 파일로 나눈다(한 파일에 업체 견적서 여러 장이 들어오는 경우). 한 쪽이면 그대로."""
    raw = base64.b64decode(data_b64)
    if not CFG.get("split_pages") or Path(name).suffix.lower() != ".pdf":
        return [{"name": name, "data": data_b64}]
    import io
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(raw))
    if len(reader.pages) < 2:
        return [{"name": name, "data": data_b64}]
    out = []
    for n, page in enumerate(reader.pages, 1):
        w = PdfWriter()
        w.add_page(page)
        buf = io.BytesIO()
        w.write(buf)
        out.append({"name": f"{Path(name).stem}_p{n}.pdf", "page": n, "data": base64.b64encode(buf.getvalue()).decode()})
    return out


def studio_quote(name, data_b64, token=None):
    """올린 견적서를 Studio 품의 Agent(API)로 읽어 견적 목록에 더한다."""
    from run_api import ROOT as _root, run_file
    if studio_missing():  # 키가 없으면 파일을 남기기 전에 멈춘다
        raise RuntimeError(config("ui")["quotes"]["studio_missing"].format(env=_root / ".env", keys=", ".join(studio_missing())))
    res_dir, src_dir, _ = CFG["quote_sets"][0]
    name = Path(name).name
    if Path(name).suffix.lower() not in CFG["quote_upload_ext"]:
        raise ValueError(config("ui")["quotes"]["upload_bad_ext"].format(ext=", ".join(CFG["quote_upload_ext"])))
    src = ROOT / src_dir / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(base64.b64decode(data_b64))
    from run_api import NotQuote, StudioError
    msg = config("ui")["quotes"]
    try:
        def progress(done, found, finished):
            if token:
                PROGRESS[token] = {"done": done, "found": found, "finished": finished}
        _, job_id = run_file(src, ROOT / res_dir, progress)
    except NotQuote as e:  # 견적서가 아니면 올린 파일을 남기지 않는다
        src.unlink(missing_ok=True)
        raise NotQuoteError(msg["not_quote"].format(category=e.category), e.category) from None
    except StudioError as e:  # 읽지 못했으면 올린 파일도 남기지 않고, 무엇을 확인할지 알려 준다
        src.unlink(missing_ok=True)
        print(f"Studio 오류: {e}", file=sys.stderr)
        raise RuntimeError(msg["studio_errors"][e.kind].format(code=e.code)) from None
    except Exception:
        src.unlink(missing_ok=True)
        raise
    res = ROOT / res_dir / f"{src.stem}.json"
    r = unwrap(json.loads(res.read_text(encoding="utf-8")))
    F = fields()
    if not (r.get(F["items"]) or r.get(F["total"])):  # 품목도 금액도 없으면 표지·안내문 같은 쪽이다
        res.unlink(missing_ok=True)
        src.unlink(missing_ok=True)
        raise NotQuoteError(msg["not_quote_empty"], msg["empty_category"])
    return add_quote(0, res, src_dir), job_id


def text_of(q):
    if q["source"] not in STATE["texts"]:
        STATE["texts"][q["source"]] = source_text(q["source"])
    return STATE["texts"][q["source"]]


def check(qid):
    q = STATE["quotes"][qid]
    recheck(q["state"], text_of(q), STATE["case"])


def groups():
    """지금 견적 목록을 같은 물품끼리 묶는다 → {id: {"group": 번호, "label": 이름}}."""
    if not STATE["quotes"]:
        return {}
    of, names = group_quotes({qid: q["state"]["result"] for qid, q in STATE["quotes"].items()})
    return {qid: {"group": n, "label": names[n]} for qid, n in of.items()}


def summary(qid):
    q = STATE["quotes"][qid]
    F, I, r = fields(), fields()["item"], q["state"]["result"]
    return {"id": qid, "set": q["set"], "name": q["name"], "has_source": bool(q["source"]),
            "vendor": r.get(F["vendor_name"]), "total": r.get(F["total"]), "supply": r.get(F["supply"]),
            "vat": r.get(F["vat"]), "quote_date": r.get(F["quote_date"]),
            "items": [{"category": it.get(I["category"]), "description": it.get(I["description"]),
                       "amount": it.get(I["amount"]), "basis": it.get(I["basis"])} for it in r.get(F["items"]) or []],
            "questions": q["state"]["questions"], "hold": q["state"]["hold"],
            "answered": q["state"]["resolution_log"], "model_flags": r.get("model_flags", []),
            "studio": r.get("studio")}


def answer(qid, key, value, who):
    st = STATE["quotes"][qid]["state"]
    qs = st["questions"]
    idx = next(i for i, x in enumerate(qs) if x["key"] == key)
    st["questions"] = [qs[idx]] + qs[:idx] + qs[idx + 1:]  # resolve()는 맨 앞 질문에 답한다
    resolve(st, coerce(qs[idx]["field"], value), who)
    check(qid)


def quote_for_select(qid):
    s = summary(qid)
    rep = STATE["quotes"][qid]["state"]["result"].get(fields()["vendor_representative"]) or ""
    return {"file": qid, "vendor": s["vendor"], "rep": rep, "total": s["total"] or 0, "quote_date": s["quote_date"] or "",
            "issues": [x["kind"] for x in s["questions"]], "issue_messages": [x["message"] for x in s["questions"]]}


# ---------------------------------------------------------------- 양식

def list_forms():
    out = []
    for d in CFG["form_dirs"]:
        for p in sorted((ROOT / d).glob("**/*.hwpx")):
            if ".form." in p.name:
                continue
            out.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"), "name": p.stem, "group": p.parent.name})
    return out


def out_url(path):
    return "/out/" + str(Path(path).resolve().relative_to(OUT.resolve())).replace("\\", "/")


def form_preview(rel):
    src = (ROOT / rel).resolve()
    dst = OUT / "forms" / (src.parent.name + "_" + src.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        dst.write_bytes(src.read_bytes())
    return out_url(preview(dst))


def do_fill(form_rel, qid):
    q = STATE["quotes"][qid]
    st = q["state"]
    if st["questions"] or st["hold"]:
        return {"ok": False, "blocked": [x["message"] for x in st["questions"]] or [config("questions")["hold_flag"]]}
    doc, report = fill(ROOT / form_rel, st["result"], STATE["case"], STATE["selection"])
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = OUT / stamp
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{STATE['case'].get('case_id', 'case')}_{Path(form_rel).stem}"
    filled = doc.save(folder / f"{name}.hwpx")
    html_file = preview(filled)  # PDF는 만들지 않는다: 미리보기를 브라우저에서 인쇄하거나 한글에서 PDF로 저장한다
    report["files"] = {"hwpx": out_url(filled), "html": out_url(html_file)}
    (folder / f"{name}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "report": report}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def file(self, base, rel):
        p = (base / unquote(rel)).resolve()
        if not str(p).startswith(str(base.resolve())) or not p.is_file():
            return self.send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix == ".hwpx":
            ctype = "application/hwp+zip"
        self.send(200, p.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self.file(UI, "index.html")
            if u.path.startswith("/ui/"):
                return self.file(UI, u.path[4:])
            if u.path.startswith("/out/"):
                return self.file(OUT, u.path[5:])
            if u.path == "/api/init":
                return self.send(200, {
                    "ui": config("ui"), "case": STATE["case"], "categories": config("categories")["categories"],
                    "funding_sources": rules()["form"]["funding_sources"],
                    "sets": [s[2] for s in CFG["quote_sets"]], "quotes": [summary(i) for i in STATE["quotes"]],
                    "forms": list_forms(), "selection": STATE["selection"], "special": {"keep": ACK, "hold": HOLD},
                    "studio_ready": not studio_missing(), "upload_workers": CFG.get("upload_workers", 1),
                    "groups": groups()})
            if u.path == "/api/quotes/progress":
                return self.send(200, PROGRESS.get(parse_qs(u.query).get("token", [""])[0]) or {"done": [], "found": {}})
            if u.path == "/api/form_preview":
                return self.send(200, {"url": form_preview(parse_qs(u.query)["form"][0])})
            self.send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            self.send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            b = self.body()
            if u.path == "/api/quotes/split":
                return self.send(200, {"pages": split_pages(b["name"], b["data"])})
            if u.path == "/api/quotes/upload":
                try:
                    qid, job_id = studio_quote(b["name"], b["data"], b.get("token"))
                except NotQuoteError as e:  # 견적서가 아닌 쪽(팩스 표지 등)은 실패가 아니라 건너뛴다
                    return self.send(200, {"skipped": str(e), "category": e.category})
                except (ValueError, RuntimeError) as e:
                    return self.send(400, {"error": str(e)})
                finally:
                    PROGRESS.pop(b.get("token"), None)
                return self.send(200, {"quote": summary(qid), "job_id": job_id, "groups": groups()})
            if u.path == "/api/answer":
                answer(b["id"], b["key"], b["value"], b.get("who") or STATE["case"].get("drafter", ""))
                return self.send(200, summary(b["id"]))
            if u.path == "/api/case":
                STATE["case"] = b["case"]
                for qid in STATE["quotes"]:
                    check(qid)
                return self.send(200, {"quotes": [summary(i) for i in STATE["quotes"]]})
            if u.path == "/api/compare":
                quotes = [quote_for_select(i) for i in b["ids"]]
                rec, auto, order, excluded = recommend(quotes)
                return self.send(200, {"quotes": quotes, "recommended": rec["file"] if rec else None, "auto_reason": auto,
                                       "excluded": [q["file"] for q in excluded], "warnings": cross_checks(quotes)})
            if u.path == "/api/select":
                quotes = [quote_for_select(i) for i in b["ids"]]
                rec, auto, _, _ = recommend(quotes)
                chosen = next(q for q in quotes if q["file"] == b["pick"])
                try:
                    STATE["selection"] = finalize(quotes, rec, auto, chosen, (b.get("reason") or "").strip(), True)
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                return self.send(200, STATE["selection"])
            if u.path == "/api/fill":
                return self.send(200, do_fill(b["form"], b["id"]))
            if u.path == "/api/upload":
                name = Path(b["name"]).name
                if not name.lower().endswith(".hwpx"):
                    return self.send(400, {"error": config("ui")["upload"]["only_hwpx"]})
                dst = ROOT / CFG["upload_dir"] / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(base64.b64decode(b["data"]))
                return self.send(200, {"forms": list_forms(), "path": str(dst.relative_to(ROOT)).replace("\\", "/")})
            self.send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            self.send(500, {"error": str(e)})


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    STATE["case"] = json.loads((ROOT / CFG["default_case"]).read_text(encoding="utf-8"))
    load_quotes()
    srv = ThreadingHTTPServer((CFG["host"], CFG["port"]), Handler)
    url = f"http://{CFG['host']}:{CFG['port']}"
    print(f"품의 agent 화면: {url}  (견적 {len(STATE['quotes'])}건)  끄려면 Ctrl+C")
    if "--open" in sys.argv[1:]:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
