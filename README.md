# 품의 Agent

**제각각인 견적서를 올리면, 검산하고, 사람이 정할 것만 묻고, 기관 양식 그대로 품의서를 채워 주는 AI 에이전트**

대울경 × Upstage AI Agent 해커톤 V.2 출품작 (품의 파트)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Upstage](https://img.shields.io/badge/Upstage-Studio%20%C2%B7%20Solar%20Pro%204-7B61FF)
![HWPX](https://img.shields.io/badge/output-HWPX%20%C2%B7%20PDF-1f9d55)

## 왜 만들었나

물품을 사려면 업체 견적서 여러 장을 받아 비교하고, 금액을 검산하고, 기관마다 다른 품의서 양식에 옮겨 적어야 합니다.
견적서는 업체마다 표, 손글씨, 스캔본, 엑셀, 팩스처럼 양식이 제각각이라 이 일이 생각보다 오래 걸립니다.
품의 Agent는 읽기와 계산, 양식 채우기를 맡고, 사람은 결정만 하도록 만들었습니다.

## 주요 기능

| 견적서 업로드 | 검토 |
|---|---|
| ![견적서 업로드](docs/images/1_upload.jpg) | ![검토](docs/images/2_review.jpg) |
| 견적서를 올리면 미리보기 옆에 Upstage Studio가 읽는 단계(문서 인식 → 분류 → 정보 추출 → 검토·판정)와 읽어 낸 업체·합계·품목이 실시간으로 뜹니다. | 단가×수량, 합계, 부가세를 원 단위로 다시 계산하고, 사람이 정해야 할 것만 선택지와 함께 묻습니다. |

| 업체 선정 | 품의서 작성 |
|---|---|
| ![업체 선정](docs/images/3_vendor.jpg) | ![품의서 작성](docs/images/4_form.jpg) |
| 유효기간 안의 견적 중 최저가 업체를 추천하고 선정 사유를 써 줍니다. | 기관 양식(HWPX)의 칸을 찾아 그대로 채우고, HWPX와 PDF로 내려받습니다. |

## 빠른 시작

**Windows:** `run.bat`을 더블클릭합니다.
**macOS / Linux:** 터미널에서 `./run.sh`를 실행합니다.

처음 실행하면 필요한 패키지를 설치하고 브라우저에서 http://127.0.0.1:8765 를 엽니다. 끄려면 창에서 `Ctrl+C`를 누릅니다.

```bash
git clone https://github.com/yoonhoc/pumui-agent.git
cd pumui-agent
./run.sh        # Windows는 run.bat
```

> **API 키가 필요합니다.** 처음 실행하면 `.env` 파일이 만들어집니다. `UPSTAGE_API_KEY=` 뒤에 **팀 조직** Upstage 키를 넣고 다시 실행하세요. `.env`는 GitHub에 올리지 않습니다.
>
> 화면이 열리면 **견적서 업로드**로 견적서 PDF(여러 장 가능)를 올립니다. 시험해 볼 견적서는 `data_team/견적서예시/`에 있습니다.

## 동작 구조

```
견적서 PDF 업로드
   │
   ▼
Upstage Studio 워크플로 ─ 업로드할 때만 실행
   문서 인식 → 분류(견적서인지) → 정보 추출 → 견적 검토 → 후속 처리 판정 → 품의 문장
   │
   ▼
이 저장소의 Python 코드 ─ 내 PC에서 실행
   원 단위 검산·질문 → 업체 추천 → 기관 양식(HWPX) 채우기 → PDF 미리보기
```

- **모델은 읽기만, 계산과 규칙은 코드가, 애매한 건 사람이** 정합니다.
- Studio 워크플로 설정의 원본은 [`workflow/품의 Agent (노드).json`](workflow/) 입니다.
- 규칙, 화면 문구, 양식 칸 이름은 모두 [`config/`](config/)에 있고 코드에 하드코딩하지 않습니다.
- 올린 견적서는 `data_upload/`, Studio 결과는 `results_upload/`에 쌓입니다(GitHub에는 올라가지 않습니다).

## 기술 스택

| 영역 | 사용 |
|---|---|
| 문서 인식·추출 | Upstage Studio (Document Parse, Classify, Information Extract) |
| 판단·문장 | Solar Pro 4 (Studio Instruct 노드) |
| 서버 | Python 표준 라이브러리 HTTP 서버 |
| 양식 | HWPX 직접 편집(lxml), PDF 미리보기(Edge/Chrome) |
| 화면 | HTML · CSS · JavaScript (프레임워크 없음) |

## 폴더 구조

```
pumui-agent/
├── run.bat, run.sh     실행
├── tools/              서버, 검산·질문(harness), 업체 선정, 양식 채우기, Studio API 호출
├── ui/                 화면
├── config/             규칙, 화면 문구, 질문 문구, 양식 칸 이름
├── workflow/           Upstage Studio 워크플로 설정
├── forms/, templates/  기관 품의서 양식
├── data_team/          팀 견적서 예시 PDF  (results_team/: Studio 결과, answers_team/: 정답지)
└── docs/               설계 기록, 화면 캡처
```

## 팀

| 이름 | 맡은 일 |
|---|---|
| 서수빈 | 기획 |
| 김재현 | 데이터 |
| 최윤호 | 품의 Agent |
| 우용재 | 결의 Agent |

품의서가 승인되고 실제 거래가 끝나면, 결의 Agent가 거래명세서·영수증을 품의서와 맞춰 지출결의서를 만듭니다.

## 더 보기

- [설계 기록](docs/DESIGN.md): 검산 규칙, 질문 방식, 양식 채우기 원리, Studio 워크플로 구성, 시험 결과
