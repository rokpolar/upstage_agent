import html
import json
import re
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

from app.adapters.docx import DocxForm
from app.adapters.files import confined, decode_data, safe_name, write_json
from app.adapters.pdf import ConversionError, html_to_pdf
from app.adapters.studio_errors import StudioError
from app.config import config
from app.domain import gyeolui
from app.domain.money import korean_amount, won


def docx_values(case):
    """결의 결과(gyeolui.form_input의 case) → DOCX 양식에 넣을 값. 금액·날짜는 config/docx_gyeolui.json formats로 쓴다."""
    fmt = config("docx_gyeolui")["formats"]
    values = {k: v for k, v in case.items() if isinstance(v, (str, int))}
    amount = case.get("amount")
    if isinstance(amount, int):
        values["amount_text"] = fmt["amount"].format(
            korean=korean_amount(amount), won=won(amount)
        )
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(case.get("resolution_date") or ""))
    if m:
        values["resolution_date_text"] = fmt["date"].format(
            y=m[1], m=int(m[2]), d=int(m[3])
        )
    return values


class ExpensesService:
    def g_text(self):
        return config("ui")["gyeolui"]

    def g_entry(self, n, name, src_dir):
        gid = f"g{n}:{name}"
        src = next(
            (
                s
                for s in (self.root / src_dir).glob(f"{name}.*")
                if s.suffix.lower() in self.cfg["gyeolui_upload_ext"]
            ),
            None,
        )
        return gid, {
            "id": gid,
            "set": n,
            "name": re.sub(r"^[0-9a-f]{32}_", "", name),
            "status": "running",
            "error": None,
            "job_id": None,
            "started": time.time(),
            "finished": None,
            "source": str(src) if src else None,
            "info_saved": False,
            "state": None,
            "link": None,
            "fill": None,
            "progress": {"done": [], "found": {}, "finished": False},
        }

    def g_load(self, n, p, src_dir):
        """Studio 결의 결과 JSON 하나를 읽어 결의 건으로 둔다. 같은 이름의 .link.json이 있으면 품의 인계 값과 대조한다."""
        link_p = p.with_name(f"{p.stem}.link.json")
        link = (
            json.loads(link_p.read_text(encoding="utf-8")) if link_p.exists() else None
        )
        gid, e = self.g_entry(n, p.stem, src_dir)
        e.update(
            status="done",
            started=None,
            link=link,
            state=gyeolui.new_state(
                json.loads(p.read_text(encoding="utf-8")), link, e["source"]
            ),
        )
        self.state["g"][gid] = e
        return gid

    def load_gyeolui(self):
        for n, (res_dir, src_dir, _label) in enumerate(self.gcfg):
            for p in sorted((self.root / res_dir).glob("*.json")):
                if (
                    not p.name.endswith(".link.json")
                    and f"g{n}:{p.stem}" not in self.state["g"]
                ):
                    self.g_load(n, p, src_dir)

    def image_pdf(self, img, dst):
        """사진(영수증 촬영본 등)을 A4 PDF 한 쪽으로 만든다(Edge/Chrome 인쇄). 증빙 묶음에 붙이기 위함."""
        page = Path(tempfile.mkdtemp()) / "page.html"
        page.write_text(
            f'<!doctype html><meta charset="utf-8"><style>@page{{size:A4;margin:10mm}}body{{margin:0}}'
            f"img{{max-width:100%;max-height:270mm;display:block;margin:auto}}</style>"
            f'<img src="{html.escape(Path(img).resolve().as_uri())}">',
            encoding="utf-8",
        )
        try:
            html_to_pdf(page, dst)
        except ConversionError:  # 브라우저가 없음
            raise ValueError(self.g_text()["need_pdf"]) from None
        finally:
            shutil.rmtree(page.parent, ignore_errors=True)
        return dst

    def checked(self, bundle):
        """API 입력 제한(형식·500 MB·1,000쪽)에 걸리면 묶음을 지우고 화면에 이유를 보여 준다."""

        try:
            self.studio.check_input(bundle)
        except ValueError:
            bundle.unlink(missing_ok=True)
            raise
        return bundle

    def build_bundle(self, files, with_pumui, dst_dir, handoff=None):
        """올린 파일들(+ 작성한 품의서 PDF)을 증빙 묶음 파일 하나로 만든다. 결의 워크플로는 파일 하나를 받는다."""
        t = self.g_text()
        if not files:
            raise ValueError(t["no_files"])
        if len(files) > self.cfg["gyeolui_max_files"]:
            raise ValueError(t["too_many"].format(n=self.cfg["gyeolui_max_files"]))
        for f in files:
            if Path(f["name"]).suffix.lower() not in self.cfg["gyeolui_upload_ext"]:
                raise ValueError(
                    t["bad_ext"].format(ext=", ".join(self.cfg["gyeolui_upload_ext"]))
                )
        first = re.sub(r"[^\w가-힣.-]+", "_", Path(files[0]["name"]).stem)[:40]
        name = f"{uuid.uuid4().hex}_{first}" + (
            f"_외{len(files) - 1}건" if len(files) > 1 else ""
        )
        dst_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp())
        try:
            parts = []
            if with_pumui:
                h = handoff or {}
                pdf = (h.get("files") or {}).get("pdf")
                if not pdf or not Path(pdf).exists():
                    raise ValueError(t["no_pumui_pdf"])
                parts.append(Path(pdf))
            for i, f in enumerate(files):
                p = work / f"{i:02d}{Path(f['name']).suffix.lower()}"
                p.write_bytes(decode_data(f["data"]))
                parts.append(p)
            if len(parts) == 1:  # 파일 하나면 그대로 보낸다(사진도 Studio가 읽는다)
                dst = dst_dir / f"{name}{parts[0].suffix}"
                shutil.copyfile(parts[0], dst)
                return self.checked(dst)
            from pypdf import PdfWriter

            w = PdfWriter()
            for p in parts:
                w.append(
                    str(
                        p
                        if p.suffix == ".pdf"
                        else self.image_pdf(p, p.with_suffix(".pdf"))
                    )
                )
            dst = dst_dir / f"{name}.pdf"
            with open(dst, "wb") as fh:
                w.write(fh)
            return self.checked(dst)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def g_run(self, gid, bundle, res_dir, link):
        def progress(done, found, finished):
            with self.lock:
                self.state["g"][gid]["progress"] = dict(
                    done=done, found=found, finished=finished
                )

        def on_job(job):
            with self.lock:
                self.state["g"][gid]["job_id"] = job

        try:
            raw, job = self.studio.run_file(
                bundle, self.root / res_dir, progress, kind="gyeolui", on_job=on_job
            )
            if link:
                write_json(self.root / res_dir / f"{bundle.stem}.link.json", link)
            state = gyeolui.new_state(raw, link, str(bundle))

            def complete():
                self.state["g"][gid].update(
                    status="done", job_id=job, finished=time.time(), state=state
                )

            self.execute(complete)
        except Exception as err:
            message = (
                config("ui")["quotes"]["studio_errors"][err.kind].format(code=err.code)
                if isinstance(err, StudioError)
                else self.g_text()["run_failed"].format(msg=str(err))
            )

            def fail():
                self.state["g"][gid].update(
                    status="error", finished=time.time(), error=message
                )

            try:
                self.execute(fail)
            except Exception:
                traceback.print_exc()
                with self.lock:
                    fail()

    def g_upload(self, b):
        if self.studio_missing("gyeolui"):
            raise ValueError("Studio API 설정을 확인하세요.")
        state, revision = self.snapshot()
        res_dir, src_dir, _ = self.gcfg[0]
        with_pumui = bool(b.get("with_pumui"))
        bundle = self.build_bundle(
            b.get("files") or [], with_pumui, self.root / src_dir, state["handoff"]
        )
        link = state["handoff"] if (b.get("link") or with_pumui) else None
        gid, e = self.g_entry(0, bundle.stem, src_dir)
        e.update(
            source=str(bundle),
            link=link,
            parts=[safe_name(f["name"]) for f in b["files"]]
            + ([self.g_text()["pumui_part"]] if with_pumui else []),
        )
        try:
            self.execute(lambda: self.state["g"].update({gid: e}), expected=revision)
        except Exception:
            bundle.unlink(missing_ok=True)
            raise
        thread = threading.Thread(
            target=self.g_run, args=(gid, bundle, res_dir, link), daemon=True
        )
        self.threads.append(thread)
        thread.start()
        return gid

    def g_summary(self, gid):
        e = self.state["g"][gid]
        out = {
            k: e.get(k)
            for k in (
                "id",
                "set",
                "name",
                "status",
                "error",
                "job_id",
                "parts",
                "progress",
            )
        }
        out["has_source"] = bool(e["source"])
        out["source_kind"] = (
            "pdf" if (e["source"] or "").lower().endswith(".pdf") else "img"
        )
        out["elapsed"] = (
            int((e["finished"] or time.time()) - e["started"]) if e["started"] else None
        )
        out["linked"] = bool(e["link"])
        st = e["state"]
        if not st:
            return out
        r = st["result"]
        out.update(
            {
                "amount": gyeolui.pay_amount(r),
                "payee": gyeolui.payee(r),
                "base": r["base"],
                "request": r["request"],
                "receipts": r["receipts"],
                "template": r["template"],
                "draft": r["draft"],
                "studio": {
                    k: r["studio"].get(k)
                    for k in (
                        "categories",
                        "reports",
                        "decisions",
                        "draft",
                        "others",
                        "job_id",
                    )
                },
                "questions": st["questions"],
                "hold": st["hold"],
                "answered": st["resolution_log"],
                "info": st["info"],
                "hints": st["hints"],
                "blocked": gyeolui.blocked(st),
                "fill": e["fill"],
                "info_saved": e.get("info_saved", False),
            }
        )
        return out

    def docx_template(self):
        """고정 지출결의서 DOCX 양식 경로(config/docx_gyeolui.json template)."""
        path = self.root / config("docx_gyeolui")["template"]
        if not path.is_file():
            raise FileNotFoundError(
                f"지출결의서 양식이 없습니다: {path.relative_to(self.root)}"
            )
        return path

    def g_template_preview(self):
        """빈 지출결의서 양식 미리보기(결의서 작성 단계에서 작성 전에 보여 준다)."""
        src = self.docx_template()
        dst = self.out / "forms" / f"{src.stem}.preview.html"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(DocxForm(src).to_html(), encoding="utf-8")
        return self.out_url(dst)

    def g_fill(self, gid, form_rel=None):
        """결의 결과(Studio 결의 워크플로 + 검증·결의 정보)로 고정 DOCX 양식을 채워 새 DOCX를 만든다."""
        state, revision = self.snapshot()
        e = state["g"][gid]
        if e["status"] != "done":
            raise ValueError("처리가 완료되지 않았습니다.")
        st = e["state"]
        if gyeolui.blocked(st):
            return {"ok": False, "blocked": gyeolui.blocked(st)}
        cfg = config("docx_gyeolui")
        result, case = gyeolui.form_input(st)
        values = docx_values(case)
        doc = DocxForm(self.docx_template())
        filled_report = doc.fill(values, cfg)
        folder = self.out / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        name = safe_name(cfg["output_name"].format(name=e["name"]))
        filled = doc.save(folder / name, cfg.get("drop_parts", ()))
        page = folder / f"{Path(name).stem}.preview.html"
        page.write_text(DocxForm(filled).to_html(), encoding="utf-8")
        report = {
            "form": cfg["template"],
            "missing": filled_report["missing"],
            "filled": filled_report["filled"],
            "notes": filled_report["notes"],
            "unplaced": [],
            "unknown_labels": [],
            "leftover": [],
            "files": {
                "docx": self.out_url(filled),
                "html": self.out_url(page),
                "name": name,
            },
        }
        record = {
            "report": report,
            "state": st,
            "form_input": {"result": result, "case": case, "values": values},
        }
        (folder / f"{Path(name).stem}.report.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        def publish():
            self.state["g"][gid]["fill"] = report["files"]
            return {"ok": True, "report": report}

        try:
            return self.execute(publish, expected=revision)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def g_download(self, gid, format):
        if format not in ("docx", "pdf"):
            raise ValueError("다운로드 형식은 DOCX 또는 PDF여야 합니다.")
        state, revision = self.snapshot()
        files = state["g"][gid].get("fill")
        if not files:
            raise ValueError("먼저 결의서를 작성하세요.")
        name = Path(files["name"]).with_suffix("." + format).name
        if format == "docx" or files.get("pdf"):
            return {"url": files[format], "name": name}
        prefix = getattr(self, "url_prefix", "") + "/out/"
        if not files["docx"].startswith(prefix):
            raise FileNotFoundError("작성한 결의서를 찾지 못했습니다.")
        source = confined(self.out, files["docx"][len(prefix):])
        folder = self.out / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        try:
            from app.adapters.docx_pdf import to_pdf

            docx = folder / source.name
            shutil.copyfile(source, docx)
            pdf, _ = to_pdf(docx)
            url = self.out_url(pdf)

            def publish():
                self.state["g"][gid]["fill"]["pdf"] = url
                return {"url": url, "name": name}

            return self.execute(publish, expected=revision)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
