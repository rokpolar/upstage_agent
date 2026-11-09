import copy
import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from app.adapters.studio import InputError, StudioClient, StudioError
from app.adapters.studio_gyeolui import STAGES, progress
from app.adapters.studio_pumui import partial
from app.config import fields, rules


def test_workflow_settings_and_independent_base_urls(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith(("UPSTAGE_", "PUMUI_", "GYEOLUI_")) or key in (
            "AGENT_ID",
            "CONFIG_ID",
        ):
            monkeypatch.delenv(key)
    (tmp_path / ".env").write_text(
        "UPSTAGE_API_KEY=file-key\nPUMUI_AGENT_ID=file-agent\nCONFIG_ID=latest\nUPSTAGE_BASE_URL=https://one.invalid/v2\n"
    )
    monkeypatch.setenv("PUMUI_AGENT_ID", "env-agent")
    first = StudioClient(tmp_path)
    settings, missing = first.settings()
    assert (
        not missing
        and settings["agent_id"] == "env-agent"
        and settings["config_id"] is None
    )
    assert settings["key"] == "file-key"
    monkeypatch.setenv("UPSTAGE_BASE_URL", "https://two.invalid/v2")
    second = StudioClient(tmp_path)
    second.settings("gyeolui")
    assert first.base == "https://one.invalid/v2"
    assert second.base == "https://two.invalid/v2"


def test_project_header_and_retry(monkeypatch, tmp_path):
    client = StudioClient(tmp_path)
    calls = []

    def urlopen(request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate",
                {"Retry-After": "4"},
                io.BytesIO(b"limited"),
            )
        return io.BytesIO(b'{"ok":true}')

    sleep = Mock()
    monkeypatch.setattr("app.adapters.studio.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("app.adapters.studio.time.sleep", sleep)
    assert client.call(
        "GET", "/responses/test", "test-key", project="project-test"
    ) == {"ok": True}
    assert len(calls) == 2
    assert all(r.get_header("X-project-id") == "project-test" for r in calls)
    sleep.assert_called_once_with(4)


def test_post_connection_failure_is_not_resubmitted(tmp_path, monkeypatch):
    client = StudioClient(tmp_path)
    call = Mock(side_effect=urllib.error.URLError("connection"))
    monkeypatch.setattr("app.adapters.studio.urllib.request.urlopen", call)
    with pytest.raises(urllib.error.URLError):
        client.call("POST", "/responses", "test-key")
    assert call.call_count == 1


@pytest.mark.parametrize(
    "code,detail,kind",
    [
        (401, "bad", "bad_key"),
        (403, "usage_limit", "quota"),
        (403, "project forbidden", "project_forbidden"),
        (404, "missing", "not_found"),
        (413, "large", "too_large"),
        (429, "rate", "rate_limit"),
        (503, "down", "server"),
    ],
)
def test_error_classification(code, detail, kind):
    assert StudioError(code, detail, "message").kind == kind


def step(kind, name="", data=None, status="completed", extra=None):
    content = {"type": "output_text", "text": json.dumps(data, ensure_ascii=False)}
    if extra:
        content["additional_values"] = {"enum": extra}
    return {"step_type": kind, "model": name, "status": status, "content": [content]}


def test_pumui_progress_fields():
    profile = rules()["runtime"]["studio"]
    F = fields()
    job = {
        "status": "in_progress",
        "output": [
            step(
                "information-extract",
                data={F["vendor_name"]: "업체", F["total"]: 100, F["items"]: [{}]},
            )
        ],
    }
    done, found = partial(job, profile)
    assert done == ["parse", "classify", "extract"]
    assert found == {"vendor": "업체", "total": 100, "items": 1}


def test_gyeolui_progress_fields_and_pending_steps():
    st = rules()["runtime"]["gyeolui_studio"]
    job = {
        "status": "in_progress",
        "output": [
            step("merge", data={"branch_keys": ["0:영수증", "1:품의서"]}),
            step(
                "instruct", "Instruct - 품의서·영수증 검증 후속 결정", {}, extra="일치"
            ),
            step(
                "instruct",
                "Instruct - 지출결의서 기입 데이터 작성",
                {},
                status="in_progress",
            ),
        ],
    }
    done, found = progress(job, st)
    assert done == ["parse", "classify", "extract", "compare"]
    assert found["docs"] == ["영수증", "품의서"] and found["comparison"] == "일치"
    job["status"] = "completed"
    assert progress(job, st)[0] == STAGES[1:]


@pytest.mark.parametrize("kind", ["pumui", "gyeolui"])
def test_progress_forces_all_includes_and_retains_project(tmp_path, monkeypatch, kind):
    client = StudioClient(tmp_path)
    client.runtime = copy.deepcopy(client.runtime)
    client.runtime["api_poll_seconds"] = 0
    document = tmp_path / "receipt.png"
    document.write_bytes(b"placeholder")
    monkeypatch.setattr(client, "check_input", lambda p: None)
    upload = Mock(return_value="file-test")
    monkeypatch.setattr(client, "upload", upload)
    calls = []

    def call(method, path, key, body=None, headers=None, project=None):
        calls.append((method, path, project))
        if method == "POST":
            return {"id": "job-test", "status": "queued"}
        return {
            "id": "job-test",
            "status": "completed",
            "output_text": "{}",
            "output": [],
        }

    monkeypatch.setattr(client, "call", call)
    events = []
    client.run_one(
        document,
        "key",
        "agent",
        "1",
        lambda *args: events.append(args),
        kind,
        project="project",
    )
    assert calls[1][1] == "/responses/job-test?include[]=all"
    assert all(project == "project" for _, _, project in calls)
    upload.assert_called_once_with(document, "key", "project")
    assert events[-1][2] is True and events[0] == ([], {}, False)


def test_invalid_format_size_and_pdf_page_limits(tmp_path):
    client = StudioClient(tmp_path)
    client.runtime = copy.deepcopy(client.runtime)
    path = tmp_path / "bad.hwp"
    path.write_bytes(b"data")
    with pytest.raises(InputError):
        client.check_input(path)
    path = tmp_path / "big.png"
    path.write_bytes(b"too big")
    client.runtime["limits"]["max_file_bytes"] = 1
    with pytest.raises(InputError):
        client.check_input(path)
    from pypdf import PdfWriter

    path = tmp_path / "pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    client.runtime["limits"]["max_file_bytes"] = 100000
    client.runtime["limits"]["max_pages"] = 1
    with pytest.raises(InputError):
        client.check_input(path)
