# 결의 설계 기록

현재 업무 규칙은 `app/domain/gyeolui.py`, Studio 결과·진행 해석은 `app/adapters/studio_gyeolui.py`, 업무 흐름은 `app/services/expenses.py`에 있다. 기존 `tools/` 명령은 유지한다. 결의 정보와 답변은 현재 페이지의 메모리에서만 유지하며 화면의 `info_saved`로 단계 이동을 판단한다. 새로고침·재접속 시 빈 상태로 시작하고 증빙·생성 문서도 이전 작업에서 복원하지 않는다.

현재 웹 화면은 고정 DOCX 양식을 사용한다. 결의 정보는 계정과목 → 적요 → 지급금액 → 지급처 → 지급방법 → 증빙 첨부 → 결의일 → 기안자 순서다. 지급금액·지급처는 읽기 전용이며 입력란 아래의 Studio 설명은 표시하지 않는다. 문서에 쓰이지 않는 추가 항목은 화면에서 숨기고 저장 값은 보존한다.

`POST /api/gyeolui/info`의 `info`는 부분 갱신이다. 보낸 키만 수정하고 빈 문자열은 해당 값만 삭제한다. 생략한 키는 그대로 둔다. Studio 보고서는 로컬 markdown-it 번들로 제목·목록·표 등을 표시하며 원문 HTML 실행과 외부 이미지 자동 로드는 막는다.

품의 검토 답변·품의 정보·업체 선정을 저장하면 현재 인계 값과 품의 완료 표시를 비운다. 품의서를 다시 생성해야 새 값과 PDF가 다음 결의에 연결된다. 이미 만든 파일과 기존 결의 건에 연결한 인계 사본은 보존한다.

품의 Agent의 원칙을 그대로 가져와 결의(지출결의서)로 넓혔다. **모델은 읽기만, 계산과 규칙은 코드가, 애매한 건 사람이.**
새로 만든 것보다 품의 코드를 다시 쓴 것이 많다.

| 품의에서 | 결의에서 그대로 쓰거나 넓힌 것 |
|---|---|
| `run_api.py` Studio 호출 | 워크플로 종류(`kind`)를 받는다. 결의는 실행 중 `include=last`로 가볍게 조회하고, 끝나면 `include=all`을 한 번 받는다. |
| `harness.py` 질문·답 기록 | `resolve`(답 기록, 보류, 확인 처리)를 그대로 쓰고, 검사 규칙만 `gyeolui.review`로 새로 둔다. |
| `fill_form.py` 양식 채우기 | 사전만 바꿔 쓴다(`config/form_roles_gyeolui.json`). 결의서용으로 넓힌 것: 제목 판정 순서(`include_first`), 품목 줄마다 같은 값 넣는 열(`row_fill`: 지급처·지급방법), 본문 `{{역할}}` 자리표시, 양식에 없어도 되는 칸(`optional`). |
| `config/questions.json` | `g_`로 시작하는 결의 질문 문구를 더했다. |
| `config/ui.json`, `ui/app.js` | 상단 업무 전환(품의/결의)과 공용 양식 판(`formPanel`)으로 바꾸고, 결의 단계는 `ui/gyeolui.js`에 둔다. |

## Studio 결의 워크플로 결과 읽기 (`gyeolui.from_steps`)

결의 워크플로(`runtime.gyeolui_studio.agent_id`)는 파일 하나(증빙 묶음)를 받아 쪽을 나누고 문서마다 다른 추출을 한다.

| 단계 종류 | 노드(예) | 읽는 곳 |
|---|---|---|
| document-parse | step_1_parse | `source_text` (근거 줄 찾기용, HTML → 줄 글) |
| merge | 품의서·영수증 추출 통합 | `studio.categories` (branch_keys: `0:영수증`, `1:결제 품의서` …) |
| information-extract | `- receipt` / `- purchase_request` / `- expense_template_inputs` | `receipts[]`(품목 줄은 `_lines`), `request`, `template` |
| instruct | 품의서 기준 영수증 통합 검증 / 후속 결정 / 회사 결제 규정 준수 검증 / 후속 결정 / 지출결의서 기입 데이터 작성 | `studio.reports`, `studio.decisions`(enum), `studio.draft` |

노드 이름 → 역할은 `rules.json runtime.gyeolui_studio.extract_roles / instruct_roles`에 있다. 이름이 바뀌어도 추출 결과 키로 역할을 다시 판단하고, 모르는 Instruct 노드는 `others`로 화면에 그대로 보인다.
추출 키 이름은 `config/gyeolui_fields.json`에 모았다(품의의 `fields.json`과 같은 방식).

## 검사 규칙 (`gyeolui.review`)

비교 기준(품의)은 앱에서 작성한 품의서(handoff)가 있으면 그것, 없으면 묶음 안 품의서다.

| 질문 | 조건 | 선택지 |
|---|---|---|
| 지급금액 불일치 | 품의·결제 증빙·거래명세서 금액이 하나로 모이지 않음 (1원도 묻는다) | 후보 금액별 적용 / 직접 입력 / 보류 |
| 품의 초과 | 정한 지급금액 > 품의 금액 | 확인 후 진행 / 보류 |
| 지급처 불일치 | (주)·㈜·띄어쓰기를 뺀 업체명이 다름 | 후보별 적용 / 직접 입력 / 보류 |
| 증빙 합계 | 공급가액 + 부가세 ≠ 합계 (허용 오차 `amount_tolerance_won`) | 유지 / 보류 |
| 증빙 품목 합 | 품목 줄 합 ≠ 공급가액 | 유지 / 보류 |
| 사후 품의 | 거래일 < 품의일 | 확인 후 진행 / 보류 |
| 취소 표시 | 증빙에 취소·환불 상태가 있음 | 확인 후 진행 / 보류 |
| 품의서·증빙 없음 | 비교 기준 또는 지출 증빙이 없음 | (품의서 없이 진행) / 보류 |
| 다른 건 묶음 | 작성한 품의서와 묶음 안 품의서의 업체·금액이 모두 다름 | 같은 건으로 진행 / 보류 |

같은 거래의 카드전표와 거래명세서 금액은 **더하지 않고** 비교한다(중복 계산 방지). 증빙 종류는 `gyeolui_fields.json receipt_kinds` 낱말로 나눈다.
Studio 판정(보완 필요 등)은 참고로 보여 주고, 결의서를 막는 것은 코드 검사의 질문뿐이다(품의와 같음).

실제 결의 워크플로 결과(`examples/gyeolui/09_맛누리케터링_결의묶음.json`)에서 Studio가 찾은 1원 차이(카드전표 2,380,401원 vs 품의서·거래명세서 2,380,400원)를 코드 검사도 같은 질문으로 잡는다.

## 결의서 채우기

`gyeolui.form_input`이 결의 상태를 품의와 같은 키(`fields.json`)로 바꿔 `fill_form.fill(..., roles_name="form_roles_gyeolui")`에 넘긴다.

- 품목 줄: 거래명세서 줄 → 결제 증빙 줄 → 품의 품목(부가세 줄 제외) 순으로 쓰고, 부가세는 한 줄로 더한다.
- 결의일·기안자는 담당자가 넣은 값만 쓴다. 비면 양식에 빈칸으로 두고 보고서의 "입력값 없음"에 올린다.
- 기본 양식 `forms/expense/지출결의서_기본양식.hwpx`는 `{{account}}` 같은 자리표시가 든 양식이다. 결재란은 `미결재`로 둔다.
- 기관 양식 중 품의서와 결의서가 한 파일에 든 것(공주·제주)은 결의서 묶음만 채운다.

자동 결재·지급·원본 증빙 수정은 하지 않는다. 만든 파일은 검토용이다.
