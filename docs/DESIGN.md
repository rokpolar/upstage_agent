# 설계 기록 (품의 파트)

> 처음 README에 쓰던 상세 기록이다. 빠른 시작과 전체 소개는 저장소의 [README](../README.md)를 본다.

현재 구현은 `app/domain`(업무 규칙), `app/services`(업무 흐름), `app/adapters`(Studio·파일·문서·SQLite), `app/web`(HTTP), `app/cli`(명령줄)로 분리되어 있다. 아래 `tools/` 명령은 호환 진입점이다. 화면은 ES modules이며 저장한 작업은 `out/state.sqlite3`에서 복원한다.

견적서 1장 → 지출품의서 초안 → 승인 → 결의 agent로 인계.
품의서 칸 구성은 `[서식 9] 지출품의서`를 따른다. 서식에 "업체별, 지출건별로 따로 작성"이라고 되어 있어서 **품의서 1건 = 업체 1곳의 견적서 1장**으로 정했다(업체 선정은 담당자가 한다).

```
견적서 PDF ─▶ [Studio] Parse ─▶ Extract(품의서 스키마) ─▶ 결과 JSON
                                                            │
                                   harness.py: 정규화 → 코드 검사 → 담당자에게 질문 → 답 반영 → 다시 검사
                                                            │  (질문이 남거나 보류면 승인 불가)
담당자 입력(구매건.json) ─────────────────────────────────▶ render_pumui.py ─▶ 지출품의서 PDF
                                                            └─▶ snapshot.json(+답변 이력) ─▶ 결의 agent(B)
```

**역할 분담: 모델은 적힌 걸 읽기만, 계산·규칙은 코드가, 애매한 건 사람에게 묻는다.**

## 하네스 (`tools/harness.py`)

검사에 걸리면 플래그만 띄우지 않고 **근거(원문 한 줄)·제안값·선택지가 있는 질문**으로 담당자에게 묻는다. 답할 때마다 처음부터 다시 검사하고, 고른 답은 `resolution_log`(누가·언제·무엇을)로 남아 결의 인계 JSON까지 따라간다. 질문 문장은 모델이 아니라 규칙으로 만든다(같은 상황엔 같은 질문).

| 검사 | 질문 예 | 선택지 |
|---|---|---|
| 단가×수량 ≠ 금액 | 3번 품목: 8,500원 × 2 = 17,000원인데 견적서엔 170,000원 | 계산값 / 견적서 값 / 재견적(보류) |
| 품목 합 ≠ 공급가액 | 품목 합 550,000원인데 공급가액 600,000원 | 품목 합계 / 견적서 값 / 보류 |
| 부가세 없음 | 부가세 표기가 없어요 | 면세(0원) / 10% 추가 / 업체 확인(보류) |
| 부가세 ≠ 10% | 부가세가 공급가액의 10.9% | 그대로 / 10%로 / 보류 |
| 합계 = 공급가액 (VAT 별도 표지) | 표지 합계금액이 부가세 별도예요. 9,020,000원으로 품의할까요? | 확정 / 직접 입력 |
| 공급가액+부가세 ≠ 합계 | 550,000 + 55,000 = 605,000원인데 합계 660,000원 | 계산값 / 견적서 값 / 보류 |
| 비목이 허용 목록 밖 | 1번 품목의 비목을 골라 주세요 | `config/categories.json` 목록 |
| **읽은 숫자가 원문에 없음** | 읽은 값 8,500원이 원문에서 보이지 않아요 | 맞아요 / 직접 입력 |
| 견적 유효기간 경과 | 2026-11-22에 끝나서 품의일보다 빨라요 | 확인 후 진행 / 재견적(보류) |
| 업체명·품목·금액 누락 | 업체명을 읽지 못했어요 | 직접 입력 / 보류 |

```bash
python tools/harness.py results/q04.json --source samples/q04.pdf --case examples/case_q01.json   # 터미널에서 묻고 답하기
python tools/harness.py results/q04.json --source samples/q04.pdf --no-input                      # 질문만 JSON으로 (웹·n8n이 화면에 띄울 용도)
python tools/render_pumui.py out/q04_resolved.json examples/case_q01.json --approve 팀장
```
원문 대조와 유효기간 검사는 텍스트가 있는 PDF(`--source`)에서만 한다(`pip install pypdf`). 스캔본은 원문 대조를 건너뛴다.

## 현재 결과

| 데이터 | 모델 단독 | 하네스 거친 뒤 (`--check`) | 담당자 질문 |
|---|---|---|---|
| 개발용 샘플 5건 (2026-11-04) | 94/95 = 98.9% | 95/95 = 100% | q03 합계 오기, q04 부가세 없음 |
| 데이터 담당 견적서 20건 (2026-11-05, 처음 보는 데이터) | 504/512 = 98.4% (표기 차이 무시 시 99.2%) | 512/512 = 100% | 2건: 02·12 견적 유효기간 경과 |

20건에서 배운 것:
- 표지에 "합계금액 (VAT 별도)"로 공급가액을 적은 4건(04·09·14·19)도 모델은 부가세 포함 합계를 맞게 뽑았다.
- 그런데 그중 3건(09·14·19)에 금액이 맞는데도 "불일치" 플래그를 붙였다. **모델이 쓴 review_flags는 믿지 않고 버린다**(`model_flags`로만 보관). 검토 사유는 코드가 다시 만든다.
- 모델이 대표자 이름에 도장 표시를 붙이거나("한도윤 인"), 산출근거 표기(`2,000부`/`2000부`, `㎡`/`m²`)가 흔들린다. `harness.normalize()`가 정리한다.
- 추출 모델은 적힌 값을 옮길 뿐 검산하지 않는다(샘플 q03의 잘못 적힌 공급가액을 그대로 가져옴). 원 단위 산술은 코드가 한다. 팀 기획서의 "원 단위 산술은 서버에서 처리" 원칙과 같다.

`품의서_extract_schema.json`에는 20건 결과를 보고 합계 설명("VAT 별도" 주의)을 보강했지만 Studio에는 아직 다시 넣지 않았다(`workflow/품의 Agent.json`이 Studio의 현재 설정).

## 전체 흐름 (2026-11-05 기준)

```
견적서 여러 장 ─▶ 품의 Agent(Studio) ─▶ 결과 JSON들 ─▶ harness.py (검사·질문)
                                                         │
                                       select_vendor.py: 비교 → 추천 → 담당자 선택/사유 입력 → selection.json
                                                         │
기관 품의서 양식(.hwpx, 아무 양식) ─▶ fill_form.py: 칸 찾기 → 채우기 → 품의서.hwpx + 미리보기 PDF
                                                         └─ 못 채운 칸·모르는 라벨 → 보고서(report.json)로 담당자에게
```

### 화면 (`tools/server.py` + `ui/`)
```bash
python tools/server.py      # http://127.0.0.1:8766
```
견적 고르기 → 검토 질문에 답하기 → 업체 고르기(추천·사유) → 구매 정보 → 양식 고르고 채우기 → `.hwpx`·PDF 받기.
**견적서 올리기**: 화면에서 PDF를 올리면 Upstage Studio 품의 Agent(Document Parse → Information Extract)를 API로 불러 그 자리에서 읽고 목록에 더한다(`data_upload/` → `results_upload/`). `.env`에 `UPSTAGE_API_KEY`가 없으면 올리기 버튼 대신 안내가 뜨고, 미리 처리해 둔 견적만 쓸 수 있다.
표준 라이브러리 서버라 설치할 것이 없다. 화면 문구는 `config/ui.json`, 포트·견적 폴더·양식 폴더는 `rules.json ui`.
화면 문구 규칙(`config/ui.json` 맨 위 `_comment`): 제목·버튼은 그룹웨어 메뉴처럼 명사형, 안내는 합니다체 한 문장 이하, 의인화·파일 경로·모델 내부 설명 금지, 담당자 용어(기안일·합계금액·비고·업로드). 검토 질문(`config/questions.json`)은 "무엇이 어떻게 다른지" 한 문장 + "값 + 적용/유지/보류" 선택지.
모양은 Apple 스토어 구입 페이지를 참고했다: 흰 바탕과 회색 판(#f5f5f7), 글자 #1d1d1f, 강조는 파랑 #0071e3 하나, 알약 버튼, 고르면 파란 테두리, 제목은 '굵은 단계 이름. 회색 설명' 두 톤. 업체 선정은 왼쪽 요약 판 + 오른쪽 선택 타일. 글꼴은 시스템 글꼴(맥은 SF), 윈도는 Pretendard(CDN). 값은 모두 `ui/tokens.css`, 모양은 `ui/style.css`.

**화면 QA 기준** (Chromium으로 확인, 2026-11-05): 320, 360, 375, 390, 414, 600, 700x500, 800x600, 900x600, 1024, 1280x720, 1366x768, 1440x900, 1920x1080에서 가로 스크롤, 잘린 요소, 숨은 단계 탭 0건. '다음' 버튼은 항상 화면 아래에 고정. 견적 목록은 페이지 대신 목록 칸만 넘어가고(머리글 고정), 품의서 미리보기는 남은 화면 높이만큼 채우며 A4 폭을 칸 폭에 맞춰 줄인다. 1920x1080, 1366x768에서는 모든 단계가 페이지 스크롤 없음. Safari·Firefox 엔진은 이 PC에 없어 아직 실기 확인 전(구형 브라우저용 대체 값은 CSS에 넣어 둠).

### 어떤 양식이든 채우기 (`tools/fill_form.py`)
- 양식 안의 표에서 **라벨 칸**(작성일자·제목·지출금액…)과 **품목 표**(머리글: 품명·수량·단가·금액…)를 `config/form_roles.json`의 동의어로 찾는다.
- 값 자리는 ①라벨 오른쪽 칸 ②같은 칸의 `라벨 : ___` ③라벨만 있는 칸이면 그 아래 줄. "년 월 일", "일금 원정", "〇〇〇" 같은 자리표시와 파란 예시 글씨는 지운다.
- 품목이 줄보다 많으면 **줄을 늘리고**, 남는 줄은 비운다. 부가세는 한 줄로 넣어 합계(부가세 포함)와 맞춘다.
- 전용 칸이 없는 값(선정 사유·목적 등)은 넓은 빈 칸이나 "끝." 앞에 `이름: 값` 줄로 적는다.
- 한 파일에 품의서·결의서가 같이 있으면 첫 품의서만 채운다. 안내문 줄(`*`, `※`)은 건드리지 않는다.
- 웹에서 받은 공공기관 양식 5종으로 시험함(`forms/web/`): 고덕 물품구매품의서, 충남 사회적기업 품의서, 공주시 지출품의서, jjct 지출품의·결의서, 삼척(결의서·공문).
- **새 양식에서 못 찾는 칸이 있으면 코드가 아니라 `form_roles.json`에 동의어를 더한다.** (cne 양식은 동의어 4개·자리표시 1개·번호 패턴 1개 추가로 해결)
- 채우기 보고서 문구(줄 늘림, 본문 칸에 적음, 칸 없음 등)도 `form_roles.json`의 `report`에 있다.
- **양식은 고정, 내용만 채운다.** 칸 너비·정렬·글꼴은 양식 것을 그대로 쓰고, 품목 표의 열 정렬은 양식의 첫 데이터 줄을 모든 줄(부가세 줄 포함)에 같게 쓴다.
- 적요 칸에는 품명과 규격만 쓴다(`item_table.item_name.strip`: '…를 위한', 끝의 '구매/구입' 제거). 제목은 `templates.title`('{사업명} {첫 품목} 외 N건 구매의 건').
- 문장 안 빈자리(`inline_blanks`): '(      ) 물품을 구매코저'의 빈 괄호는 품목 요약, '2023.  .' 같은 빈 날짜 줄은 기안일로 채운다. '[예시]' 표시는 지운다. '접수 (   )'처럼 문맥 낱말이 없는 칸은 그대로.
- 작성 안내문('*', '※'로 시작하는 줄과 같은 칸의 그 뒤 줄)은 건드리지 않는다.
- 채운 뒤 양식에 남은 예시 값(날짜, 금액 등, `leftover_check`)은 보고서의 '양식에 남은 예시 값 (확인 필요)'에 올린다. 예: 삼척 양식은 예시가 채워진 견본이라 2024년 날짜·현수막 금액이 남는다고 알려 준다.
- 미리보기(`tools/hwpx_preview.py`)는 칸 격자에서 열 너비를 풀어 `<colgroup>`으로 그리고, 줄 높이·칸 여백·문단 정렬·글자 크기를 양식대로 반영한다.
- 한글 없이 동작한다(보안 확인창 없음). 결과는 `.hwpx`(한글에서 바로 열림) + 미리보기 PDF. `.hwp` 양식은 한글에서 "다른 이름으로 저장 → HWPX"로 한 번 바꿔 두거나 `--use-hwp`(한글 자동화, 확인창 뜰 수 있음).

```bash
python tools/select_vendor.py results/a.json results/b.json results/c.json --source a.pdf b.pdf c.pdf --case examples/case_team01.json
python tools/fill_form.py forms/web/cne/품의서양식.hwpx results/a.json examples/case_team01.json --selection out/selection.json
```

### 업체 선정 (`tools/select_vendor.py`)
- 견적마다 하네스 검사를 돌려 문제(유효기간 경과 등)를 함께 보여주고, `rules.json vendor_selection` 규칙(제외 조건 → 최저가 → 최근 견적)으로 추천한다.
- 추천 업체를 고르면 자동 사유("비교견적 3개 업체 중 최저가(…), 차순위 대비 …원 저렴, 견적 유효기간 내"), **다른 업체를 고르면 사유 입력이 필수**.

## 설정 (하드코딩 금지)

규칙·키 이름·문구·양식은 코드에 두지 않는다. 바꿀 일이 있으면 아래 파일만 고친다.

| 파일 | 내용 | 주로 고치는 사람 |
|---|---|---|
| `config/rules.json` | 부가세율, 허용 오차(원), 도장 표시, 원문 패턴("VAT 별도", 유효기간), 품의서 기본값(결재란·재원), 양식 폴더, API 주소·시간, PDF 변환 브라우저 경로 | 개발 |
| `config/fields.json` | 역할 → 실제 키 이름 (Studio 스키마·정답지·결의 인계 JSON의 키) | 개발 (스키마를 바꿀 때) |
| `config/questions.json` | 담당자에게 보이는 질문·선택지 문구 | 기획 |
| `config/categories.json` | 비목 허용 목록 | 기획·회계 |
| `config/form_roles.json` | 양식 칸 찾기 사전(라벨 동의어, 품목 표 머리글, 자리표시, 값 형식, 본문 칸 규칙) | 기획 (새 양식이 올 때) |
| `config/ui.json` | 웹 화면 문구 | 기획 |
| `templates/지출품의서/` | 지출품의서 양식(`page.html` 칸 배치, `parts.json` 반복 조각) | 기획 |
| `.env` | API 키, Agent ID, (선택) `UPSTAGE_BASE_URL` | 각자 |

코드에 남아 있는 것은 알고리즘뿐이다: 금액 한글 표기, 날짜·숫자 정규화, 견적서 표 텍스트 파서(`answers_from_text.py`, 사람 검토용 정답 초안 생성기), 시험용 가상 견적서 데이터(`gen_samples.py`의 SAMPLES).

## 파일

| 경로 | 내용 |
|---|---|
| `품의서_extract_schema.json` | Studio Extract 노드에 "JSON 추출 항목 가져오기"로 넣는 스키마 |
| `workflow/품의 Agent.json` | Studio에서 내보낸 워크플로우 설정 (Mission 2 `팀명_미션2workflow.json`의 원본) |
| `results/q01~q05.json`, `results_team/` | Studio 품의 Agent 실행 결과 (샘플 5건, 데이터 담당 20건) |
| `samples/q01~q05.pdf` | 개발용 가상 견적서 5건 (양식 4종, 예외 2건) |
| `answers/q01~q05.json` | 위 견적서의 정답 (스키마와 같은 키) |
| `tools/gen_samples.py` | 가상 견적서와 정답을 같은 데이터에서 함께 생성 |
| `tools/score.py` | Studio 결과 JSON과 정답 비교, `score_report.csv` 출력 |
| `tools/render_pumui.py` | 결과 JSON + 담당자 입력 → 지출품의서 PDF, 결의 인계 JSON |
| `tools/harness.py` | 결과 검사 → 담당자 질문 → 답 반영 → 재검사 (아래 "하네스") |
| `tools/run_api.py` | 브라우저 없이 Upstage API로 품의 Agent 실행 (`.env`에 API 키) |
| `tools/answers_from_text.py` | 텍스트 PDF에서 정답 초안 생성 (사람 검토용, 비목 제외) |
| `config/`, `templates/` | 규칙·키 이름·문구·양식 (위 "설정" 참고) |
| `data_team/견적서예시/`, `answers_team/` | 데이터 담당이 만든 견적서 20건과 정답 초안 |
| `examples/` | 담당자 입력 예시, 승인된 품의서 PDF·인계 JSON 예시 |

## 출력 키 (= 정답지 키)

| 서식 칸 | 키 | 형식 |
|---|---|---|
| 거래처 | `vendor_name`, `vendor_representative`, `quote_date` | 문자열, 날짜는 YYYY-MM-DD |
| 내역 행 | `items[]` | 견적서 품목 한 줄당 한 행 |
| └ 비목 | `expense_category` | 사무용품비 / 홍보비 / 장비구입비 / 용역비 / 임차료 / 기타 |
| └ 적요 | `description` | 자유 문장 (채점 제외, 유사도만 참고) |
| └ 추정금액 | `quantity`, `unit_price`, `estimated_amount` | 쉼표 없는 정수 |
| └ 산출근거 | `calculation_basis` | `4,000원*100장` |
| └ 비고 | `note` | 없으면 빈 문자열 |
| 합계(부가세포함) | `supply_amount`, `vat_amount`, `total_amount` | 쉼표 없는 정수 |
| 검토 필요 | `review_flags[]` | 문제가 없으면 빈 배열 |

정답 규칙: 금액은 **견적서에 적힌 값 그대로**, 맞지 않으면 `review_flags`에 사유. 부가세 표기가 없으면 `vat_amount=0` + `"부가세 표기 없음"`.
재원구분·단위사업명·사업기간·관련계획서·결재란은 견적서에 없으므로 agent가 아니라 담당자 입력(`examples/case_q01.json`)으로 받는다.

## Studio 노드 워크플로 "품의 Agent (노드)" (2026-11-05)

Studio 캔버스로 만든 노드 구성. 원본은 [workflow/품의 Agent (노드).json](../workflow/품의%20Agent%20(노드).json)(Studio "워크플로우 설정 내보내기" 형식)이고,
고칠 때는 이 파일을 고친 뒤 Studio 워크플로 메뉴의 "워크플로우 설정 가져오기"로 올린다(이전 설정은 Studio 버전 기록에 남는다).

```
시작 → 인식(Parse) → 문서 분류(Classify: 견적서 / 거래명세서 / 기타)
     → quote(Extract, 품의서_extract_schema.json) → 견적 검토(Instruct)
     → 후속 처리 판정(Instruct, 결정: 작성 가능 / 담당자 확인 필요 / 재견적 필요 / 작성 중지)
         ├ 작성 가능         → 품의 내용 작성(제목, 품목 요약)
         ├ 담당자 확인 필요  → 담당자 질문 작성
         ├ 재견적 필요       → 재견적 요청 작성(업체에 보낼 문안)
         └ 작성 중지         → 작성 중지 사유
```

- Instruct 모델은 `solar-pro4`(지정하지 않으면 Studio 기본값 GPT 5.4로 돈다). 프롬프트의 `{{vendor_name}}` 등은 quote 추출 값.
- 이중 확인: Studio 노드가 판정하고, 앱의 하네스가 원 단위로 다시 검산한다. 실제로 첫 구성(GPT 5.4)은 q03의 공급가액 불일치(품목 합계 550,000원 ≠ 600,000원)를 놓쳤고, 계산을 먼저 적게 한 뒤에야 잡았다.
- Studio Instruct 출력에는 근거 표시(【†1】)가 붙을 수 있다. 앱에서 받을 때 지운다.
- 비용: 2026-11-05 실행분(인식, 분류, 추출, Instruct 토큰) 모두 $0.00(AI Initiative).
- 시험: 하나문구유통(작성 가능), q03(재견적 필요), q04(담당자 확인 필요) 3건 판정이 모두 맞음.

**앱 연결** (`tools/run_api.py`, `config/rules.json` runtime.studio):
- `runtime.studio.agent_id`가 있으면 .env의 AGENT_ID 대신 이 노드 워크플로를 부른다. `config_id`가 null이면 Studio 기본(최근 저장) 설정.
- `include: ["all"]`로 단계별 결과를 받아, 추출 JSON에 `"studio": {category, review, decision, branch, note}`를 붙여 저장한다. 근거 표시는 `citation_pattern`으로 지운다.
- 견적서가 아니라고 분류되면(거래명세서 등) 목록에 넣지 않고 화면에 이유를 알린다. 올린 파일도 남기지 않는다.
- 검토 화면에 견적마다 "Studio 판정"과 분기 노드 결과(재견적 요청 문안, 담당자 질문 등)를 펼쳐 볼 수 있게 보여 준다. 질문과 금액 확정은 앱 하네스가 한다.

```
python tools/run_api.py data_team/견적서예시 --out results_team                  # 전부 다시(동시 5건)
python tools/run_api.py data_team/견적서예시 --out results_team --skip-existing  # 결과 없는 것만
```

## Studio에서 만들기

이미 만든 워크플로우를 다른 스페이스(팀 스페이스)로 옮기려면 워크플로우 목록의 **워크플로우 설정 일괄 가져오기**로 `workflow/품의 Agent.json`을 불러오면 된다. 새로 만들 때는:

1. Studio → AI 워크플로우 → 워크플로우 만들기 → **문서 정보 추출** → 이름 `품의 Agent`
2. `samples/q01~q05.pdf`를 한 번에 업로드 → 설정 방식에서 **직접 설정**
3. 워크플로우 편집기에서 Extract 노드 선택 → 추출 항목 오른쪽 `⋯` → **JSON 추출 항목 가져오기** → `품의서_extract_schema.json`
4. 노드 이름 `Extract-품의서` 확인 → 편집기 닫기 → **실행**

주의: Studio는 목록 항목(`review_flags`)의 **목록 쪽 설명을 버리고 항목 쪽 설명만** 남긴다. 그래서 검토 사유 규칙은 `review_flags.items.description`에 적어 두었다.
5. 결과 패널 JSON 탭 → 다운로드 버튼으로 받아 `results/q01.json`으로 저장. q02~q05도 업로드해 반복
6. 오른쪽 위 `⋯` → **워크플로우 설정 내보내기** (Mission 2의 `팀명_미션2workflow.json`)

API로 한 번에 돌리려면 스타터 코드의 `run_agent.py samples/`를 쓰고 결과 폴더를 `results/`로 두면 된다.

## 실행

```bash
python tools/run_api.py data_team/견적서예시 --out results_team    # API 실행 (.env에 UPSTAGE_API_KEY)
python tools/score.py results_team answers_team --check
python tools/score.py results answers
python tools/score.py results answers --check
python tools/render_pumui.py results/q01.json examples/case_q01.json --out out
python tools/render_pumui.py results/q01.json examples/case_q01.json --out out --approve 팀장
python tools/gen_samples.py
```

- `score.py`: 금액·수량은 정수로 정확히, 업체명·날짜·비목·산출근거는 공백·`(주)/㈜/주식회사`·날짜 구분자를 맞춘 뒤 비교. `review_flags`는 검토 필요 여부(비었는지)만 채점.
- `render_pumui.py`: 렌더 전에 하네스 검사를 다시 돌린다. 답하지 않은 질문이나 보류가 있으면 `--approve`를 거부한다.
- API 비용: 2026-11-05 Console Usage 확인 결과, 품의 Agent의 Document Parse·Information Extract 사용분이 Studio 화면·API 실행 모두 **$0.00**("UPSTAGE X AWS AI INITIATIVE" 적용, 크레딧 차감 없음). API 실행 시간은 1쪽 견적서 기준 24~139초.
- PDF 변환에 Edge 또는 Chrome이 필요하다. 외부 패키지는 쓰지 않는다(Python 3.10+).

## 결의 agent(B)로 넘기는 인계 JSON

`examples/CASE-2026-0001_snapshot.json` 참고. 결의 쪽 1원 대조의 "품의 승인액"은 `total_amount`.

```json
{ "case_id": "CASE-2026-0001", "approval_version": 1, "status": "APPROVED",
  "approved_by": "팀장", "approved_at": "2026-11-05T10:00:00+09:00",
  "vendor_name": "㈜진주인쇄", "items": [ ... ],
  "supply_amount": 900000, "vat_amount": 90000, "total_amount": 990000, "review_flags": [] }
```

## 설계서에 쓸 내용 (Mission 2)

- **업무**: 견적서를 보고 지출품의서의 내역·금액·산출근거를 옮겨 적는 일
- **문서 선정 이유**: 품의는 지출의 출발점이고, 견적서는 업체마다 양식이 달라 손으로 옮겨 적는 시간이 들며 합계 오기도 생긴다
- **노드 구성**: Parse(표·스캔 읽기) → Extract(서식 칸에 맞춘 스키마). v0는 견적서 1장 단위라 Classify·Merge가 필요 없다. 업체 비교·선정은 담당자 판단으로 남긴다
- **검토 필요 설계**: 금액은 추측하지 않고 적힌 대로 뽑은 뒤, 부가세 누락 같은 판독 문제는 Extract가, 합계 불일치 같은 산술 문제는 코드 검산이 `review_flags`로 사람에게 넘긴다. 플래그가 있으면 승인 불가
- **테스트 결과**: 샘플 5건에서 Extract 단독 98.9%, 코드 검산 포함 100%. 유일한 오답(q03 합계 오기 미검출)이 산술을 코드로 분리한 근거
- **테스트 유형**: 단순(q01), 양식 다양성(q02 줄별 세액, q05 총액 상단·한글 금액), 예외(q03 합계 오기, q04 부가세 없음). 저품질 스캔·문서 결합은 데이터 담당 세트로 추가
