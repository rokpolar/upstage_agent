"""Run the published n8n webhooks and adapt their results to the app contract."""

import base64
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from pypdf import PdfReader

from app.adapters.files import write_json
from app.adapters.studio_errors import InputError, NotQuote
from app.config import ROOT, config, fields, rules
from app.domain.procurement import cfg as purchase_config
from app.domain.procurement import check_quote, is_amount, selected_result

FORMATS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_PAGES = 25
MAX_BODY = 15 * 1024 * 1024
DEFAULT_WEBHOOK = "http://127.0.0.1:5678/webhook"
PATHS = {
    "pumui": "upstage-agent/quote",
    "gyeolui": "upstage-agent/expense",
    "purchase": "upstage-agent/purchase",
}
TOKEN_HEADER = "X-Workflow-Key"


class WorkflowError(ValueError):
    """An n8n transport or model error safe to show in the app."""


def workflow_env(root=ROOT):
    names = {"WORKFLOW_BACKEND", "N8N_WEBHOOK_BASE_URL", "N8N_WEBHOOK_KEY"}
    env = {}
    path = Path(root) / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() in names:
                env[name.strip()] = value.strip().strip('"').strip("'")
    env.update({name: os.environ[name] for name in names if os.environ.get(name)})
    return env


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class N8nClient:
    name = "n8n"
    formats = FORMATS

    def __init__(self, root=ROOT, runtime=None):
        self.env = workflow_env(root)
        self.runtime = runtime if runtime is not None else rules()["runtime"]
        self.opener = urllib.request.build_opener(NoRedirect)

    def settings(self, kind="pumui"):
        return self.env, (
            [] if self.env.get("N8N_WEBHOOK_KEY") else ["N8N_WEBHOOK_KEY"]
        )

    def check_input(self, path):
        path = Path(path)
        if path.suffix.lower() not in self.formats:
            raise InputError("n8n은 PDF, PNG, JPEG 파일을 읽을 수 있습니다.")
        if path.stat().st_size > self.runtime["limits"]["max_file_bytes"]:
            raise InputError("파일이 너무 큽니다. 파일을 나누어 올려 주세요.")
        if path.suffix.lower() == ".pdf":
            try:
                count = len(PdfReader(str(path)).pages)
            except Exception:
                raise InputError(
                    "PDF를 읽을 수 없습니다. 파일과 암호 설정을 확인하세요."
                ) from None
            if not 0 < count <= MAX_PAGES:
                raise InputError(f"PDF는 1~{MAX_PAGES}쪽까지 올릴 수 있습니다.")

    def pages(self, path, sources=None):
        path = Path(path)
        if path.suffix.lower() != ".pdf":
            if path.stat().st_size * 4 // 3 >= MAX_BODY:
                raise InputError("이미지가 너무 큽니다. 크기를 줄여 다시 올려 주세요.")
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            return [page_image(path, 1, mime, sources[0] if sources else None)]
        with tempfile.TemporaryDirectory(prefix="n8n-pages-") as folder:
            try:
                subprocess.run(
                    [
                        "pdftoppm",
                        "-scale-to",
                        "2000",
                        "-jpeg",
                        "-jpegopt",
                        "quality=85",
                        str(path),
                        str(Path(folder) / "page"),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=90,
                )
            except FileNotFoundError:
                raise WorkflowError(
                    "PDF를 읽으려면 Poppler의 pdftoppm을 설치하세요."
                ) from None
            except subprocess.TimeoutExpired:
                raise WorkflowError(
                    "PDF를 이미지로 변환하는 시간이 초과되었습니다."
                ) from None
            except subprocess.CalledProcessError:
                raise InputError("PDF를 이미지로 변환하지 못했습니다.") from None
            images = sorted(
                Path(folder).glob("page-*.jpg"),
                key=lambda image: int(image.stem.rsplit("-", 1)[-1]),
            )
            if not images or (sources and len(sources) != len(images)):
                raise InputError("PDF 쪽을 읽을 수 없습니다.")
            if sum(image.stat().st_size for image in images) * 4 // 3 >= MAX_BODY:
                raise InputError(
                    "변환한 문서가 너무 큽니다. 파일을 나누어 올려 주세요."
                )
            return [
                page_image(
                    img, pos, "image/jpeg", sources[pos - 1] if sources else None
                )
                for pos, img in enumerate(images, 1)
            ]

    def call(self, kind, payload):
        key = self.env.get("N8N_WEBHOOK_KEY")
        if not key:
            raise WorkflowError(".env에 N8N_WEBHOOK_KEY를 설정하세요.")
        base = self.env.get("N8N_WEBHOOK_BASE_URL") or DEFAULT_WEBHOOK
        url = urlsplit(base)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.query
            or url.fragment
            or url.username
        ):
            raise WorkflowError("N8N_WEBHOOK_BASE_URL에 올바른 웹훅 주소를 입력하세요.")
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > MAX_BODY:
            raise InputError(
                "n8n에 보낼 데이터가 너무 큽니다. 파일을 나누어 올려 주세요."
            )
        request = urllib.request.Request(
            f"{base.rstrip('/')}/{PATHS[kind]}",
            data,
            {"Content-Type": "application/json", TOKEN_HEADER: key},
            method="POST",
        )
        try:
            with self.opener.open(
                request, timeout=self.runtime["api_timeout_seconds"]
            ) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            reason = {
                401: "워크플로 웹훅 인증 설정을 확인하세요.",
                403: "워크플로 웹훅 인증 설정을 확인하세요.",
                404: "n8n에서 워크플로를 가져와 게시했는지 확인하세요.",
                413: "전송할 데이터가 너무 큽니다.",
            }
            raise WorkflowError(
                reason.get(exc.code, f"n8n 웹훅 오류(HTTP {exc.code}).")
            ) from None
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, TimeoutError) or isinstance(exc.reason, TimeoutError):
                raise WorkflowError(
                    "n8n 워크플로 응답 시간이 초과되었습니다."
                ) from None
            raise WorkflowError(
                "n8n에 연결할 수 없습니다. 웹훅 주소와 서비스 상태를 확인하세요."
            ) from None
        except (ValueError, UnicodeError):
            raise WorkflowError("n8n 응답이 올바른 JSON이 아닙니다.") from None
        if not isinstance(data, dict):
            raise WorkflowError("n8n 응답 형식이 올바르지 않습니다.")
        errors = {
            "model_auth": "n8n의 모델 API 자격증명을 확인하세요.",
            "model_timeout": "모델 응답 시간이 초과되었습니다.",
            "model_rate_limit": "모델 호출 한도에 도달했습니다. 잠시 뒤 다시 시도하세요.",
            "model_error": "모델 호출에 실패했습니다. n8n의 모델 주소와 모델명을 확인하세요.",
            "invalid_model_output": "모델이 읽을 수 있는 문서 결과를 반환하지 않았습니다.",
            "invalid_input": "n8n에 전달한 입력 형식이 올바르지 않습니다.",
            "purchase_error": "구매 건 판정에 실패했습니다. 앱 연결과 입력을 확인하세요.",
        }
        error = data.get("error")
        if isinstance(error, str):
            raise WorkflowError(errors.get(error, "n8n 워크플로 실행에 실패했습니다."))
        return data

    def run_file(
        self, path, out_dir, progress=None, kind="pumui", on_job=None, *, sources=None
    ):
        self.check_input(path)
        images = self.pages(path, sources)
        job = uuid.uuid4().hex
        if on_job:
            on_job(job)
        # Synchronous webhooks expose confirmed milestones at the response boundary.
        if progress:
            progress(["parse"], {}, False)
        response = self.call(kind, {"request_id": job, "pages": images})
        if response.get("request_id") != job:
            raise WorkflowError("n8n 응답의 작업 ID가 일치하지 않습니다.")
        category = response.get("category")
        if kind == "pumui":
            if category in ("거래명세서", "기타"):
                raise NotQuote(category)
            if category != "견적서":
                raise WorkflowError("모델 응답의 문서 분류가 올바르지 않습니다.")
        result = response.get("result")
        validate_result(result, kind)
        result["studio"]["job_id"] = job
        result["backend"] = self.name
        write_json(Path(out_dir) / f"{Path(path).stem}.json", result)
        if progress:
            if kind == "pumui":
                stage = ["upload", "parse", "classify", "extract", "instruct"]
                f = fields()
                found = {
                    "category": category,
                    "vendor": result.get(f["vendor_name"]),
                    "total": result.get(f["total"]),
                    "items": len(result[f["items"]]),
                }
            else:
                from app.adapters.studio_gyeolui import STAGES

                stage = STAGES
                found = {"docs": result["studio"]["categories"]}
            progress(stage, found, True)
        return result, job

    def purchase(self, rows):
        result = self.call("purchase", {"rows": rows})
        try:
            check_purchase_response(result, rows)
        except (KeyError, TypeError, ValueError):
            raise WorkflowError(
                "구매 건 판정 응답이 입력 견적과 일치하지 않습니다."
            ) from None
        return result


def check_purchase_response(response, submitted):
    """Check that cases account for every submitted page and use valid selections."""
    pending = {
        (row["file"], row["page"]): (row.get("quote"), row.get("category"))
        for row in submitted
    }

    def check_page(row):
        file, page, quote, category = row
        if type(page) is not int or pending.pop((file, page)) != (quote, category):
            raise ValueError

    failed = [
        {"file": row["file"], "page": row["page"], "error": row["error"]}
        for row in submitted
        if row.get("error") is not None
    ]
    if (
        not isinstance(response["cases"], list)
        or not isinstance(response["loose"], list)
        or response["failed_pages"] != failed
    ):
        raise ValueError
    decisions = purchase_config()["decisions"]
    for case in response["cases"]:
        result, pages = case["result"], case["rows"]
        if not isinstance(pages, list) or not pages:
            raise ValueError
        for page in pages:
            check_page(page)
        names = sorted({page[0] for page in pages})
        if (
            result["file"] != (names[0] if len(names) == 1 else names)
            or result["decision"] not in decisions.values()
            or not isinstance(result["reason"], str)
            or not isinstance(result["selected_vendor"], str)
            or not is_amount(result["selected_total"])
            or type(result["quote_count"]) is not int
            or result["quote_count"] != sum(page[2] is not None for page in pages)
            or not isinstance(result["needs_review"], list)
            or not isinstance(result["documents"], list)
        ):
            raise ValueError
        if (
            result["selected_quote"] is not None
            or result["decision"] == decisions["ok"]
        ):
            selected_result(result, pages)
        elif result["selected_total"] is not None:
            raise ValueError
    for page in response["loose"]:
        check_page(page)
        if page[2] is not None:
            raise ValueError
    if pending:
        raise ValueError


def page_image(path, number, mime, source):
    source = source or {"document": 1, "page": number}
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"number": number, **source, "data_url": f"data:{mime};base64,{encoded}"}


def validate_result(result, kind):
    def objects(value):
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)

    def strings(value):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)

    error = WorkflowError("모델 응답의 문서 결과 형식이 올바르지 않습니다.")
    if not isinstance(result, dict) or not isinstance(result.get("studio"), dict):
        raise error
    meta = result["studio"]
    if kind == "pumui":
        try:
            check_quote(result)
        except ValueError:
            raise error from None
        if not strings(result.get(fields()["flags"])):
            raise error
        if not all(
            isinstance(meta.get(k, ""), str)
            for k in ("kind", "review", "decision", "note", "branch")
        ):
            raise error
        return
    requests, receipts = result.get("requests"), result.get("receipts")
    if not objects(requests) or not objects(receipts):
        raise error
    if any(not objects(item.get("_lines")) for item in receipts):
        raise error
    request_fields = config("gyeolui_fields")["request"]
    if any(not objects(item.get(request_fields["items"])) for item in requests):
        raise error
    template = result.get("template")
    if template is not None and (
        not isinstance(template, dict) or not objects(template.get("input_fields"))
    ):
        raise error
    if not strings(meta.get("categories")) or not isinstance(
        result.get("source_text"), str
    ):
        raise error
    if not isinstance(meta.get("draft"), str):
        raise error
    for key in ("reports", "decisions"):
        if not isinstance(meta.get(key), dict) or not all(
            isinstance(v, str) for v in meta[key].values()
        ):
            raise error
    result["request"] = requests[0] if requests else None


def workflow_client(root=ROOT, runtime=None):
    backend = workflow_env(root).get("WORKFLOW_BACKEND") or "n8n"
    if backend == "n8n":
        return N8nClient(root, runtime)
    if backend == "studio":
        from app.adapters.studio import StudioClient

        return StudioClient(root, runtime)
    raise ValueError("WORKFLOW_BACKEND는 n8n 또는 studio여야 합니다.")
