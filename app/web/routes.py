"""기존 JSON API와 애플리케이션 서비스를 연결한다."""

import copy
import io
import math
import zipfile

from app.adapters.files import decode_data, safe_name, unique_name
from app.config import config, rules
from app.domain.harness import ACK, HOLD
from app.domain.select_vendor import recommend


def require(body, key, kind):
    if key not in body or not isinstance(body[key], kind):
        raise ValueError(f"요청 항목 '{key}'의 형식이 올바르지 않습니다.")
    return body[key]


def answer_value(body):
    if "value" not in body:
        raise ValueError("답변 값이 필요합니다.")
    value = body["value"]
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (str, int, float))
    ):
        raise ValueError("답변은 문자열 또는 숫자여야 합니다.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("유한한 숫자가 필요합니다.")
    return value


class Routes:
    def __init__(self, app):
        self.app = app

    def init(self):
        a = self.app
        with a.lock:
            return copy.deepcopy(
                {
                    "ui": config("ui"),
                    "case": a.state["case"],
                    "categories": config("categories")["categories"],
                    "funding_sources": rules()["form"]["funding_sources"],
                    "sets": [s[2] for s in a.cfg["quote_sets"]],
                    "quotes": [a.summary(i) for i in a.state["quotes"]],
                    "forms": a.list_forms(),
                    "selection": a.state["selection"],
                    "special": {"keep": ACK, "hold": HOLD},
                    "studio_ready": not a.studio_missing(),
                    "upload_workers": a.cfg.get("upload_workers", 1),
                    "groups": a.groups(),
                    "gyeolui": {
                        "sets": [s[2] for s in a.gcfg],
                        "items": [a.g_summary(i) for i in a.state["g"]],
                        "forms": a.list_forms("gyeolui"),
                        "default_form": a.cfg["gyeolui_default_form"],
                        "ready": not a.studio_missing("gyeolui"),
                        "handoff": a.handoff_summary(),
                        "accept": a.cfg["gyeolui_upload_ext"],
                        # 결의서 양식은 DOCX 하나로 고정(config/docx_gyeolui.json)
                        "template": config("docx_gyeolui")["template"].rsplit("/", 1)[-1],
                    },
                }
            )

    def get(self, path, query):
        a = self.app
        if path == "/healthz":
            return {"ok": True}
        if path == "/api/init":
            return self.init()
        if path == "/api/quotes/progress":
            with a.lock:
                return copy.deepcopy(
                    a.progress.get(query.get("token", [""])[0])
                    or {"done": [], "found": {}}
                )
        if path == "/api/gyeolui/status":
            with a.lock:
                return copy.deepcopy(
                    {
                        "items": [
                            a.g_summary(i)
                            for i in query.get("id", [])
                            if i in a.state["g"]
                        ]
                    }
                )
        if path == "/api/gyeolui/template_preview":
            return {"url": a.g_template_preview()}
        if path == "/api/form_preview":
            if not query.get("form"):
                raise ValueError("양식 경로가 필요합니다.")
            return {"url": a.form_preview(query["form"][0])}
        raise FileNotFoundError("not found")

    def post(self, path, body):
        a = self.app
        if not isinstance(body, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        if path == "/api/quotes/upload":
            name, data = require(body, "name", str), require(body, "data", str)
            if "token" in body:
                require(body, "token", str)
            qid, job = a.studio_quote(name, data, body.get("token"))
            with a.lock:
                return copy.deepcopy(
                    {"quote": a.summary(qid), "job_id": job, "groups": a.groups()}
                )
        if path == "/api/case":
            return a.execute(a.set_case, require(body, "case", dict))
        if path in ("/api/compare", "/api/select"):
            ids = require(body, "ids", list)
            if not ids or any(not isinstance(i, str) for i in ids):
                raise ValueError("비교할 견적 목록이 필요합니다.")
            with a.lock:
                self.check_ids(ids)
                if path == "/api/select":
                    pick = require(body, "pick", str)
                    reason = body.get("reason") or ""
                    if not isinstance(reason, str):
                        raise ValueError("선정 사유는 문자열이어야 합니다.")
                    return a.execute(a.select, ids, pick, reason)
                quotes = [a.quote_for_select(i) for i in ids]
                rec, auto, _, excluded = recommend(quotes)
                return {
                    "quotes": quotes,
                    "recommended": rec["file"] if rec else None,
                    "auto_reason": auto,
                    "excluded": [q["file"] for q in excluded],
                }
        if path == "/api/answer":
            qid, key = require(body, "id", str), require(body, "key", str)
            value = answer_value(body)
            who = body.get("who") or ""
            if not isinstance(who, str):
                raise ValueError("담당자는 문자열이어야 합니다.")

            def answer():
                self.check_ids([qid])
                a.answer(qid, key, value, who or a.state["case"].get("drafter", ""))
                return a.summary(qid)

            return a.execute(answer)
        if path == "/api/fill":
            qid, form = require(body, "id", str), require(body, "form", str)
            with a.lock:
                self.check_ids([qid])
            return a.do_fill(form, qid)
        if path == "/api/gyeolui/upload":
            files = require(body, "files", list)
            for entry in files:
                if not isinstance(entry, dict):
                    raise ValueError("파일 항목이 올바르지 않습니다.")
                require(entry, "name", str)
                decode_data(require(entry, "data", str))
            for key in ("link", "with_pumui"):
                if key in body:
                    require(body, key, bool)
            gid = a.g_upload(body)
            with a.lock:
                return {"item": copy.deepcopy(a.g_summary(gid))}
        if path in ("/api/gyeolui/answer", "/api/gyeolui/info", "/api/gyeolui/fill"):
            gid = require(body, "id", str)
            with a.lock:
                self.check_ids([gid], "g")
            if path.endswith("/info"):
                return a.execute(a.g_info, gid, require(body, "info", dict))
            if path.endswith("/answer"):
                key = require(body, "key", str)
                value = answer_value(body)
                who = body.get("who") or ""
                if not isinstance(who, str):
                    raise ValueError("담당자는 문자열이어야 합니다.")
                return a.execute(a.g_answer, gid, key, value, who)
            return a.g_fill(gid)  # 양식은 고정 DOCX라 고르지 않는다
        if path == "/api/upload":
            name = safe_name(require(body, "name", str))
            if not name.lower().endswith(".hwpx"):
                raise ValueError(config("ui")["upload"]["only_hwpx"])
            data = decode_data(require(body, "data", str))
            if not zipfile.is_zipfile(io.BytesIO(data)):
                raise ValueError("올바른 HWPX 파일이 아닙니다.")
            dst = a.root / a.cfg["upload_dir"] / unique_name(name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            return {
                "forms": a.list_forms(),
                "gyeolui_forms": a.list_forms("gyeolui"),
                "path": dst.relative_to(a.root).as_posix(),
            }
        raise FileNotFoundError("not found")

    def check_ids(self, ids, group="quotes"):
        if any(i not in self.app.state[group] for i in ids):
            raise FileNotFoundError("작업을 찾지 못했습니다.")
