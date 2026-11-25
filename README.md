<div align="center">

# Upstage Agent

품의·지출결의 문서 작성 및 검증

![Python 3.11 이상](assets/readme/python.svg)
![Docker Compose](assets/readme/docker.svg)
![출력 DOCX · PDF](assets/readme/output.svg)

[주요 기능](#주요-기능) · [시스템 구조](#시스템-구조) · [빠른 시작](#빠른-시작) · [기술 스택](#기술-스택) · [개발 안내](#개발-안내)

</div>

견적서 검토부터 품의서·지출결의서 작성까지 연결하는 웹 애플리케이션입니다. n8n 워크플로의 문서 추출 결과를 코드로 검증하고, 담당자가 확정한 정보를 고정 양식에 반영합니다.

## 주요 기능

| 입력 | 산출물 |
|---|---|
| 견적서 | 물품구매품의서 |
| 품의서 및 거래 증빙 | 지출결의서 |

- 품의: 동일 물품의 견적을 비교하고, 금액·유효기간을 검토해 업체를 선정합니다.
- 결의: 품의 내용과 영수증·카드전표·거래명세서·세금계산서를 대조합니다.
- 업무 연결: 작성한 품의서의 금액·업체·품목과 PDF를 결의 단계로 전달합니다.

![견적 검토 화면: 유효기간이 지난 견적을 표시하고 담당자에게 진행 또는 보류를 요청합니다.](docs/images/2_review.jpg)

견적 검토 예시 — 유효기간이 지난 견적은 담당자의 확인을 거쳐 진행합니다.

<details>
<summary>업로드·업체 선정 화면</summary>

![견적서 업로드 화면](docs/images/1_upload.jpg)

![업체 선정 화면](docs/images/3_vendor.jpg)

</details>

## 시스템 구조

앱이 PDF를 페이지 이미지로 변환하면 n8n이 비전 모델로 문서를 분류하고 값을 추출합니다. Python이 금액과 업무 규칙을 검증하고 담당자가 확인을 마치면 DOCX·PDF를 생성합니다. 결의에 파일을 여러 개 올리면 앱이 증빙 묶음을 만들고 원본 파일과 쪽의 순서를 n8n에 전달합니다.

- 품의 검증: 단가 × 수량, 합계·부가세, 견적 유효기간, 최저가 동률, 동일 대표자 업체를 확인합니다.
- 결의 검증: 품의·증빙 금액과 지급처 불일치, 초과 집행, 증빙 산술, 사후 품의, 취소·환불 표시를 확인합니다.

금액이 1원이라도 다르면 확인 후보를 표시합니다. 납품·검수·계좌 정보와 서명 등 확인되지 않은 값은 자동 생성하지 않습니다.

## 빠른 시작

### 1. Docker Compose 시작

```bash
cp .env.example .env
openssl rand -hex 32
```

생성한 값을 `.env`의 `N8N_WEBHOOK_KEY`에 넣고 실행합니다.

```bash
docker compose up -d --build --wait
```

### 2. n8n 워크플로 게시

[n8n 편집기](http://127.0.0.1:5678)에서 로컬 관리자 계정을 만든 뒤 다음 설정을 적용합니다.

1. [n8n/workflows/](n8n/workflows/)의 `quote.json`, `expense.json`, `purchase.json`을 각각 **Import from File**로 가져옵니다.
2. Header Auth 자격증명을 만들고 헤더 이름을 `X-Workflow-Key`, 값을 `.env`의 `N8N_WEBHOOK_KEY`와 같게 입력합니다. 세 워크플로의 **Webhook** 노드와 구매 건 판정의 **Call Python purchase rules** 노드에 연결합니다.
3. 별도의 Header Auth 자격증명에 이름 `Authorization`과 값 `Bearer <모델 API 키>`를 입력합니다. 견적서·지출결의의 **Call vision model** 노드에 연결합니다. 같은 노드의 URL과 JSON Body의 `model`을 제공자에 맞춥니다. 예제는 OpenAI의 `gpt-4.1-mini`이며 제공자는 Chat Completions, Base64 이미지 입력, JSON object 응답을 지원해야 합니다.
4. 세 워크플로를 **Publish**합니다. [앱](http://127.0.0.1:8766)에서 PDF·PNG·JPEG를 올립니다. n8n 모드의 파일 하나는 최대 25쪽이며 변환 후 요청 본문은 최대 15 MiB입니다.

Docker의 앱은 `http://n8n:5678/webhook`에 연결합니다. 호스트에서 실행하는 앱·CLI는 `N8N_WEBHOOK_BASE_URL`을 사용합니다. 기본값은 `http://127.0.0.1:5678/webhook`입니다. 포트를 바꿨다면 웹훅 URL도 맞추세요.

로컬 앱은 Python 3.11 이상, 문서 이미지 변환용 Poppler의 `pdftoppm`, 양식 PDF 생성용 Chrome 또는 Chromium이 필요합니다. macOS·Linux는 `./run.sh`, Windows는 `run.bat`으로 실행합니다. 호스트 앱만 실행하는 경우 구매 건 판정 노드의 URL도 n8n에서 접근 가능한 앱 주소로 바꿉니다. Docker Desktop의 예는 `http://host.docker.internal:8766/api/n8n/purchase`입니다.

<details>
<summary>기존 Upstage Studio로 실행</summary>

`.env`에 `WORKFLOW_BACKEND=studio`와 [Upstage API 키](https://console.upstage.ai/api-keys)를 설정하고 `docker compose up -d --build --force-recreate app`을 실행합니다. Agent ID와 Config ID는 Studio의 `API로 이용하기`에서 확인합니다. Studio로 가져올 원본 설정은 [workflow/](workflow/)에 있습니다.

| 설정 | 품의 | 결의 |
|---|---|---|
| API 키 | `PUMUI_API_KEY` | `GYEOLUI_API_KEY` |
| Agent ID | `PUMUI_AGENT_ID` | `GYEOLUI_AGENT_ID` |
| Config ID | `PUMUI_CONFIG_ID` | `GYEOLUI_CONFIG_ID` |
| Project ID | `PUMUI_PROJECT_ID` | `GYEOLUI_PROJECT_ID` |

- 개별 API 키를 비우면 `UPSTAGE_API_KEY`, 개별 Project ID를 비우면 `UPSTAGE_PROJECT_ID`를 사용합니다.
- Agent·Config ID를 비우면 [`config/rules.json`](config/rules.json)의 기본값을 사용합니다. `latest`는 최신 Studio 설정을 실행합니다.
- 같은 이름의 환경변수가 있으면 `.env`보다 우선합니다.

</details>

### 3. 예시 문서로 시작

- 품의: [`data_team/견적서예시/`](data_team/견적서예시/)의 견적서를 올립니다.
- 결의: [`data_team/결의묶음예시/`](data_team/결의묶음예시/)의 품의서·증빙 묶음을 올립니다.
- 추가 증빙: [`data_team/거래명세서/`](data_team/거래명세서/)에서 확인합니다.

문서를 업로드하고 확인 사항을 처리한 뒤, 생성된 DOCX·PDF를 내려받습니다. n8n 모드에서는 업로드한 문서 이미지가 설정한 모델 제공자에 전송됩니다. Compose의 n8n은 완료·실패한 실행 데이터를 저장하지 않으며, 자격증명과 워크플로는 Docker 볼륨에 저장합니다.

## 운영 및 데이터 관리

- 작업 수명: 업로드 파일, 입력 값, 분석 결과와 생성 문서는 현재 페이지의 임시 작업 공간에만 유지합니다. 품의·결의 메뉴 간 이동 시 작업은 유지되며, 새로고침·재접속 시 초기화됩니다.
- 파일 정리: 페이지 종료 시 임시 파일을 삭제합니다. 종료 알림이 없으면 마지막 요청·연결 확인 후 2분이 지나면 정리합니다. 처리 중인 요청은 완료 후 정리합니다.
- 저장 위치: Docker에서는 `/tmp`의 RAM 파일시스템을 사용합니다. 필요한 문서는 페이지를 닫기 전에 내려받으세요.
- 출력 형식: 웹 화면은 고정 DOCX 양식을 사용합니다. PDF는 DOCX를 HTML로 변환한 뒤 브라우저로 생성하므로 Word 원본과 배치가 다를 수 있습니다.

## 기술 스택

| 영역 | 기술 |
|---|---|
| 문서 인식·추출 | n8n · OpenAI 호환 비전 모델 |
| 업무 검증 | Python 규칙 · 담당자 확인 |
| 백엔드 | Python 3.11+ · 표준 라이브러리 HTTP 서버 |
| 프론트엔드 | HTML · CSS · JavaScript ES modules |
| 문서 처리 | `lxml` · `pypdf` · Poppler · Chrome/Chromium |
| 작업 상태 | 메모리 · 페이지별 임시 파일 |
| 실행·배포 | Docker Compose |

## 개발 안내

| 경로 | 내용 |
|---|---|
| [`app/`](app/) | 업무 규칙, 서비스, 외부 연동, 웹·CLI |
| [`ui/`](ui/) | 품의·결의 화면 |
| [`config/`](config/) | 검증 규칙, 화면 문구, 양식 필드 설정 |
| [`forms/`](forms/) | 품의·결의 양식 |
| [`n8n/workflows/`](n8n/workflows/) | n8n으로 가져올 워크플로 JSON |
| [`workflow/`](workflow/) | Studio 품의 워크플로 원본 설정 |
| [`tools/`](tools/) | 서버·검증·배치 실행 명령 |

설계와 세부 규칙은 [품의 설계](docs/DESIGN.md), [결의 설계](docs/GYEOLUI.md), [지출결의서 양식 안내](forms/expense/README.md)를 참고하세요.

<details>
<summary>개발 환경과 검사</summary>

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check app tests tools
```

`run.sh`·`run.bat`은 `.venv`가 있으면 해당 Python을 사용합니다. 테스트는 임시 작업 폴더와 모의 Studio 응답을 사용하며 실제 문서를 API로 제출하지 않습니다. HTTP 테스트에는 로컬 포트 사용 권한이 필요하고, 화면 모듈 테스트에는 Node.js가 필요합니다.

</details>

<details>
<summary>명령줄 실행</summary>

아래 문서 실행 명령은 `WORKFLOW_BACKEND`의 연결 설정을 사용합니다. n8n 모드에서 `run_set.py`는 견적서 검토와 구매 건 판정 웹훅을 호출합니다.

```bash
# 구매 건 단위 판정과 정답지 대조
python tools/run_set.py data_team/견적서예시/*.pdf --no-form

# Studio에 가져올 품의 판정 워크플로 설정 생성
python tools/make_set_workflow.py

# 결의 워크플로 실행
python tools/run_api.py data_team/결의묶음예시 --kind gyeolui --out results_gyeolui_upload

# 저장된 결과로 검증 질문 확인
python tools/gyeolui.py examples/gyeolui/09_맛누리케터링_결의묶음.json --ask

# 같은 물품 묶기 채점
python tools/grouping.py results_team --check
```

CLI가 저장한 결과는 웹 페이지의 임시 작업 수명과 별개입니다. `python tools/make_n8n_workflows.py`는 저장소의 추출 계약을 사용해 n8n JSON을 다시 생성합니다.

세 웹훅은 Header Auth를 사용하는 JSON POST입니다. `/webhook/upstage-agent/quote`와 `/webhook/upstage-agent/expense`는 `request_id`와 `pages`를 받습니다. 각 쪽의 `number`는 묶음 내 순서, `document`와 `page`는 원본 파일 번호와 쪽 번호입니다. `data_url`은 PNG 또는 JPEG의 Base64 데이터 URL입니다. `/webhook/upstage-agent/purchase`의 `rows`에는 `file`, `page`, `quote`, `category`를 보냅니다. 분류에서 제외한 쪽은 `quote: null`과 분류 값을 보냅니다. 처리에 실패한 쪽은 `error`도 보내며, 결과의 `failed_pages`에 기록됩니다. 실패한 쪽이 영향을 줄 수 있는 구매 건은 담당자 확인으로 남기고, 작성 가능한 건은 `selected_quote`의 파일과 쪽 번호로 양식을 채웁니다.

같은 이름의 PDF를 바꾸면 파일 내용에 맞는 새 캐시가 사용됩니다. `--fresh`는 PDF 분할과 인식을 다시 실행합니다. 일부 쪽의 처리에 실패하면 배치는 가능한 건의 결과를 저장하고 종료 코드 1을 반환합니다.

</details>

<details>
<summary>Docker 운영</summary>

```bash
docker compose logs -f app                  # 로그 확인
docker compose down                         # 종료
docker compose up -d --force-recreate        # .env 변경 반영
APP_DOCKER_PORT=8767 docker compose up -d     # 호스트 포트 변경
```

`.env`는 이미지에 넣지 않고 환경변수로 전달합니다. Docker와 로컬 실행의 기본 포트가 같으므로 동시에 실행하려면 포트를 변경하세요.

</details>

## 팀

대울경 × Upstage AI Agent 해커톤 V.2 출품작입니다. [품의 Agent](https://github.com/yoonhoc/pumui-agent)를 기반으로 품의·결의 업무를 통합했습니다.

| 이름 | 역할 |
|---|---|
| 서수빈 | 기획 |
| 김재현 | 데이터 |
| 최윤호 | 품의 Agent |
| 우용재 | 결의 Agent |
