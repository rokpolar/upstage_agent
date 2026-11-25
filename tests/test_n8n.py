import json
from io import BytesIO
from urllib.error import URLError

import pytest

from app.adapters.n8n import N8nClient, WorkflowError, workflow_client
from app.adapters.studio import StudioClient
from app.adapters.studio_errors import InputError, NotQuote
from app.config import ROOT
from app.domain.procurement import evaluate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_KEY", "test-key")
    return N8nClient(tmp_path)


def test_backend_selection(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKFLOW_BACKEND", raising=False)
    assert isinstance(workflow_client(tmp_path), N8nClient)
    (tmp_path / ".env").write_text("WORKFLOW_BACKEND=studio\n")
    assert isinstance(workflow_client(tmp_path), StudioClient)
    monkeypatch.setenv("WORKFLOW_BACKEND", "n8n")
    assert isinstance(workflow_client(tmp_path), N8nClient)


def test_quote_webhook_saves_existing_result_shape(client, tmp_path, monkeypatch):
    image = tmp_path / "견적서.png"
    image.write_bytes(b"fixture")
    sample = json.loads((ROOT / "results/q01.json").read_text())
    progress = []
    jobs = []

    def open_request(request, timeout):
        assert request.get_header("X-workflow-key") == "test-key"
        assert request.full_url.endswith("/webhook/upstage-agent/quote")
        data = json.loads(request.data)
        assert data["pages"] == [
            {
                "number": 1,
                "document": 1,
                "page": 1,
                "data_url": "data:image/png;base64,Zml4dHVyZQ==",
            }
        ]
        result = {**sample, "studio": {"category": "견적서", "kind": "인쇄"}}
        return BytesIO(
            json.dumps(
                {
                    "request_id": data["request_id"],
                    "category": "견적서",
                    "result": result,
                }
            ).encode()
        )

    monkeypatch.setattr(client.opener, "open", open_request)
    out = tmp_path / "out"
    result, job = client.run_file(
        image, out, lambda *event: progress.append(event), on_job=jobs.append
    )
    assert jobs == [job]
    assert progress[0] == (["parse"], {}, False)
    assert progress[-1][1]["total"] == sample["total_amount"]
    assert progress[-1][2]
    assert result["studio"]["job_id"] == job
    assert result["backend"] == "n8n"
    assert json.loads((out / "견적서.json").read_text()) == result


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"category": "기타"}, NotQuote),
        (
            {"category": "견적서", "result": {"studio": {}, "items": [{}]}},
            WorkflowError,
        ),
        ({"category": "견적서", "result": None}, WorkflowError),
    ],
)
def test_rejected_and_invalid_results_leave_no_file(
    client, tmp_path, monkeypatch, payload, error
):
    image = tmp_path / "document.png"
    image.write_bytes(b"fixture")
    monkeypatch.setattr(
        client, "call", lambda _, body: {"request_id": body["request_id"], **payload}
    )
    with pytest.raises(error):
        client.run_file(image, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_timeout_is_distinct_from_model_auth_failure(client, monkeypatch):
    def timeout(*args, **kwargs):
        raise URLError(TimeoutError())

    monkeypatch.setattr(client.opener, "open", timeout)
    with pytest.raises(WorkflowError, match="시간이 초과"):
        client.call("pumui", {})

    monkeypatch.setattr(
        client.opener, "open", lambda *a, **kw: BytesIO(b'{"error":"model_auth"}')
    )
    with pytest.raises(WorkflowError, match="모델 API 자격증명"):
        client.call("pumui", {})


def test_invalid_pdf_is_rejected_before_webhook(client, tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"fixture")
    with pytest.raises(InputError, match="PDF를 읽을 수 없습니다"):
        client.check_input(path)


def test_purchase_does_not_select_vendor_when_one_quote_is_available():
    sample = json.loads((ROOT / "results/q01.json").read_text())
    result = evaluate(
        [
            {"file": "set.pdf", "page": 1, "quote": None, "category": "FAX 송부장"},
            {"file": "set.pdf", "page": 2, "quote": sample, "category": None},
        ]
    )["cases"][0]["result"]
    assert result["documents"] == ["FAX 송부장", "견적서"]
    assert (
        result["decision"],
        result["selected_vendor"],
        result["selected_total"],
    ) == (
        "작성 중지",
        "확인 불가",
        None,
    )


@pytest.mark.parametrize(
    "malformation", ["missing page", "wrong selection", "wrong quote count"]
)
def test_purchase_rejects_invalid_decision_response(client, monkeypatch, malformation):
    sample = json.loads((ROOT / "results/q01.json").read_text())
    rows = [
        {"file": "set.pdf", "page": 1, "quote": sample, "category": None},
        {
            "file": "set.pdf",
            "page": 2,
            "quote": {
                **sample,
                "vendor_name": "다른 업체",
                "total_amount": 2_000_000,
            },
            "category": None,
        },
    ]
    response = json.loads(json.dumps(evaluate(rows)))
    monkeypatch.setattr(client, "call", lambda *args: response)
    assert client.purchase(rows) == response

    case = response["cases"][0]
    if malformation == "missing page":
        case["rows"].pop()
    elif malformation == "wrong selection":
        case["result"]["selected_quote"]["page"] = 2
    else:
        case["result"]["quote_count"] = "2"
    with pytest.raises(WorkflowError, match="입력 견적과 일치하지 않습니다"):
        client.purchase(rows)
