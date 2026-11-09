"""Studio 품의·결의 워크플로를 Upstage API로 실행해 결과 JSON으로 저장한다 (브라우저 없이).

    python tools/run_api.py samples              # 폴더 안 문서 전부
    python tools/run_api.py samples/q01.pdf      # 문서 1개
    python tools/run_api.py samples --score      # 실행 후 answers/와 채점(코드 검산 포함)
    python tools/run_api.py data_team/견적서예시 --out results_team --skip-existing   # 결과 없는 것만, 여러 건 동시에
    python tools/run_api.py data_team/결의묶음예시 --kind gyeolui --out examples/gyeolui   # 결의 워크플로

워크플로 설정은 rules.json runtime에 있다: 품의 = runtime.studio, 결의 = runtime.gyeolui_studio.
결의 결과(단계별 원본)는 tools/gyeolui.py from_steps가 읽어 {request, receipts, template, studio}로 저장한다.

노드 워크플로(rules.json runtime.studio)면 단계별 결과를 받아 추출 JSON에 "studio" 칸을 붙인다:
  {"category": 분류, "review": 견적 검토, "decision": 후속 처리 판정, "branch": 분기 노드 이름, "note": 분기 노드 결과}
견적서가 아니라고 분류되면 NotQuote 오류를 낸다.

Upstage Studio Agent API(OpenAI 호환)를 표준 라이브러리만으로 호출한다.
  1) POST /v2/files          문서 업로드 → file_id
  2) POST /v2/responses      Agent 실행(job 생성) → job_id
  3) GET  /v2/responses/{id} completed/failed까지 조회 → output_text(JSON)
모든 요청에 Authorization: Bearer <UPSTAGE_API_KEY>, Agent가 프로젝트에 속하면 x-project-id를 보낸다
(헤더 없이 올린 파일은 프로젝트 밖에 저장되어 그 Agent가 쓰지 못한다).

설정은 워크플로마다 따로 둔다. 환경변수 > .env > config/rules.json 순으로 쓴다(키는 화면에 출력하지 않는다).
  품의  PUMUI_AGENT_ID(또는 AGENT_ID)  PUMUI_CONFIG_ID(또는 CONFIG_ID)  PUMUI_PROJECT_ID   PUMUI_API_KEY
  결의  GYEOLUI_AGENT_ID               GYEOLUI_CONFIG_ID               GYEOLUI_PROJECT_ID GYEOLUI_API_KEY
  공통  UPSTAGE_API_KEY(필수), UPSTAGE_PROJECT_ID(워크플로별 값이 없을 때), UPSTAGE_BASE_URL
CONFIG_ID를 latest로 두면 config_id를 보내지 않아 최신 설정으로 돈다.
입력 제한(형식·500 MB·1,000쪽)은 올리기 전에 검사하고, 429·5xx는 잠깐 기다렸다 다시 보낸다(rules.json runtime.retry).
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.adapters.studio import DOC_EXT, PROFILES, NotQuote, profile, run_one, settings
from app.config import ROOT


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("target", help="문서 파일 또는 폴더")
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--score", action="store_true", help="실행 후 answers/와 채점")
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="이미 끝난 문서는 건너뛴다(노드 워크플로면 Studio 판정이 있는 결과만)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        help="동시에 돌릴 문서 수(기본: rules.json runtime.studio.workers)",
    )
    ap.add_argument(
        "--kind",
        choices=sorted(PROFILES),
        default="pumui",
        help="pumui=견적서→품의, gyeolui=증빙 묶음→결의",
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    s, missing = settings(a.kind)
    if missing:
        sys.exit(f"{ROOT / '.env'}에 {', '.join(missing)} 값을 넣어 주세요.")
    key, agent_id, config_id, project = (
        s["key"],
        s["agent_id"],
        s["config_id"],
        s["project_id"],
    )

    target = Path(a.target)
    files = (
        sorted(p for p in target.iterdir() if p.suffix.lower() in DOC_EXT)
        if target.is_dir()
        else [target]
    )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(
        f"Agent {agent_id} (config {config_id or 'latest'}, project {project or '-'}) · 문서 {len(files)}건"
    )

    if (
        a.skip_existing
    ):  # 노드 워크플로면 Studio 판정("studio")까지 있는 결과만 끝난 것으로 본다
        node = bool(profile(a.kind).get("agent_id"))

        def done(f):
            p = out / f"{f.stem}.json"
            return p.exists() and (
                not node or "studio" in json.loads(p.read_text(encoding="utf-8"))
            )

        files = [f for f in files if not done(f)]
    workers = a.workers or profile(a.kind).get("workers", 1)
    print(f"실행할 문서 {len(files)}건, 동시 {workers}건")

    failed = []

    def one(f):
        t = time.time()
        try:
            result, job_id = run_one(
                f, key, agent_id, config_id, kind=a.kind, project=project
            )
        except NotQuote as e:
            return f, f"견적서 아님({e.category})", None
        except Exception as e:  # 한 건이 실패해도 나머지는 계속 돌린다
            return f, f"실패: {e}", None
        (out / f"{f.stem}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        st = result.get("studio") or {}
        decision = st.get("decision") or " / ".join(
            (st.get("decisions") or {}).values()
        )
        return f, f"완료 {time.time() - t:.0f}초  {decision}  ({job_id})", True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (f, msg, ok) in enumerate(pool.map(one, files), 1):
            print(f"[{i}/{len(files)}] {f.name}  {msg}", flush=True)
            if not ok:
                failed.append(f.name)

    print(f"\n완료 {len(files) - len(failed)}건 / 실패 {len(failed)}건")
    if a.score and a.kind == "pumui" and len(files) > len(failed):
        print()
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "score.py"),
                str(out),
                str(ROOT / "answers"),
                "--check",
            ]
        )
    sys.exit(1 if failed else 0)
