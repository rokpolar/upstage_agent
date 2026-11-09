import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

from app.adapters.files import safe_name
from app.adapters.fill_form import Roles, build_values, fill
from app.adapters.hwpx_preview import preview as hwpx_preview


def preview(path, pdf=False):
    """양식 미리보기(HTML). pdf=True면 PDF도 만든다(결의 쪽 증빙 묶음에 붙이는 용도).
    Word는 tools/docx_pdf.py(rules.json preview.docx_pdf.order 순서), HWPX는 미리보기 인쇄."""
    if Path(path).suffix.lower() != ".docx":
        return hwpx_preview(path, pdf)
    from app.adapters.docx_preview import preview as docx_html

    html_file = docx_html(path)
    if not pdf:
        return html_file
    from app.adapters.docx_pdf import to_pdf

    return to_pdf(path)[0]
from app.config import config
from app.domain import gyeolui


class FormsService:
    def list_forms(self, kind="pumui"):
        """양식 목록. 결의는 forms/expense·업로드 양식과, 이름에 '결의'가 든 기관 양식만(rules.json ui.gyeolui_form_filter)."""
        out = []
        dirs = (
            self.cfg["gyeolui_form_dirs"]
            if kind == "gyeolui"
            else self.cfg["form_dirs"]
        )
        filters = self.cfg.get("gyeolui_form_filter", {}) if kind == "gyeolui" else {}
        ext = self.cfg.get("gyeolui_form_ext" if kind == "gyeolui" else "form_ext", ".hwpx")
        for d in dirs:
            for p in sorted((self.root / d).glob(f"**/*{ext}")):
                if ".form." in p.name or (
                    d in filters and not re.search(filters[d], p.stem)
                ):
                    continue
                out.append(
                    {
                        "path": str(p.relative_to(self.root)).replace("\\", "/"),
                        "name": re.sub(r"^[0-9a-f]{32}_", "", p.stem),
                        "group": p.parent.name,
                    }
                )
        return out

    def out_url(self, path):
        return "/out/" + str(
            Path(path).resolve().relative_to(self.out.resolve())
        ).replace("\\", "/")

    def form_preview(self, rel):
        src = self.form_path(rel)
        dst = (
            self.out
            / "forms"
            / (hashlib.sha256(src.read_bytes()).hexdigest() + src.suffix.lower())
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.write_bytes(src.read_bytes())
        return self.out_url(preview(dst))

    def do_fill(self, form_rel, qid):
        state, revision = self.snapshot()
        form_path = self.form_path(form_rel)
        q = state["quotes"][qid]
        st = q["state"]
        if st["questions"] or st["hold"]:
            return {
                "ok": False,
                "blocked": [x["message"] for x in st["questions"]]
                or [config("questions")["hold_flag"]],
            }
        doc, report = fill(form_path, st["result"], state["case"], state["selection"])
        stamp = uuid.uuid4().hex
        folder = self.out / stamp
        folder.mkdir(parents=True, exist_ok=True)
        name = safe_name(
            f"{state['case'].get('case_id', 'case')}_{Path(form_rel).stem}"
        )
        filled = doc.save(folder / f"{name}{form_path.suffix.lower()}")
        pdf = preview(filled, pdf=True)  # 결의 쪽 증빙 묶음 앞에 붙일 품의서 PDF
        if not filled.with_suffix(".preview.html").exists():
            preview(filled)
        report["files"] = {
            "doc": self.out_url(filled),
            "pdf": self.out_url(pdf),
            "html": self.out_url(filled.with_suffix(".preview.html")),
            "ext": filled.suffix,
        }
        (folder / f"{name}.report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 결의로 넘길 값: 품의 금액·업체·품목 + 이 품의서 PDF(증빙 묶음 앞에 붙일 수 있다)

        title = (
            build_values(st["result"], state["case"], state["selection"], Roles()).get(
                "title"
            )
            or {}
        ).get("value")
        state["handoff"] = gyeolui.handoff(
            st["result"],
            {**state["case"], "title": title or state["case"].get("title")},
            state["selection"],
            {"pdf": str(pdf), "doc": str(filled), "form": form_rel},
        )
        (folder / f"{name}.handoff.json").write_text(
            json.dumps(state["handoff"], ensure_ascii=False, indent=2), encoding="utf-8"
        )

        def publish():
            self.state["handoff"] = state["handoff"]
            self.state["quotes"][qid]["fill"] = report
            return {"ok": True, "report": report, "handoff": self.handoff_summary()}

        try:
            return self.execute(publish, expected=revision)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def handoff_summary(self):
        h = self.state["handoff"]
        if not h:
            return None
        return {
            k: h.get(k)
            for k in (
                "case_id",
                "doc_no",
                "title",
                "vendor",
                "total",
                "draft_date",
                "at",
            )
        } | {
            "has_pdf": bool(h.get("files", {}).get("pdf"))
            and Path(h["files"]["pdf"]).exists()
        }
