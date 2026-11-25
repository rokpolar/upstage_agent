"""품의·결의 워크플로를 실행해 결과 JSON으로 저장한다 (브라우저 없이).

    python tools/run_api.py samples              # 폴더 안 문서 전부
    python tools/run_api.py samples/q01.pdf      # 문서 1개
    python tools/run_api.py samples --score      # 실행 후 answers/와 채점(코드 검산 포함)
    python tools/run_api.py data_team/견적서예시 --out results_team --skip-existing   # 결과 없는 것만, 여러 건 동시에
    python tools/run_api.py data_team/결의묶음예시 --kind gyeolui --out examples/gyeolui   # 결의 워크플로

연결 설정은 README의 n8n 또는 Studio 항목을 참조한다.
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.adapters.n8n import N8nClient, workflow_client
from app.adapters.studio import DOC_EXT, PROFILES
from app.adapters.studio_errors import NotQuote
from app.config import ROOT, rules


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
        help="현재 백엔드로 처리한 문서는 건너뛴다",
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

    client = workflow_client()
    _, missing = client.settings(a.kind)
    if missing:
        sys.exit(f"{ROOT / '.env'}에 {', '.join(missing)} 값을 넣어 주세요.")
    n8n = isinstance(client, N8nClient)
    extensions = client.formats if n8n else DOC_EXT

    target = Path(a.target)
    files = (
        sorted(p for p in target.iterdir() if p.suffix.lower() in extensions)
        if target.is_dir()
        else [target]
    )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{'n8n' if n8n else 'Upstage Studio'} · 문서 {len(files)}건")

    if a.skip_existing:

        def done(f):
            path = out / f"{f.stem}.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            return isinstance(data, dict) and (
                data.get("backend") == "n8n"
                if n8n
                else "studio" in data and data.get("backend") != "n8n"
            )

        files = [f for f in files if not done(f)]
    workers = a.workers or rules()["runtime"][PROFILES[a.kind]].get("workers", 1)
    print(f"실행할 문서 {len(files)}건, 동시 {workers}건")

    failed = []

    def one(f):
        t = time.time()
        try:
            result, job_id = client.run_file(f, out, kind=a.kind)
        except NotQuote as e:
            return f, f"견적서 아님({e.category})", None
        except Exception as e:  # 한 건이 실패해도 나머지는 계속 돌린다
            return f, f"실패: {e}", None
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
