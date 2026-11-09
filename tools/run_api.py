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

스타터 코드(run_agent.py)와 같은 API를 표준 라이브러리만으로 호출한다.
  1) POST /v2/files          문서 업로드 → file_id
  2) POST /v2/responses      Agent 실행(job 생성) → job_id
  3) GET  /v2/responses/{id} 완료될 때까지 조회 → output_text(JSON)
API 키·Agent ID는 품의agent/.env 에서 읽는다(키는 화면에 출력하지 않는다).
API 주소·조회 간격·제한 시간은 .env(UPSTAGE_BASE_URL) 또는 config/rules.json runtime에서 읽는다.
"""
import argparse
import json
import mimetypes
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import ROOT, fields, rules

DOC_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".pptx", ".xlsx", ".hwp", ".hwpx"}
BASE = None  # main()에서 .env 또는 rules.json으로 정한다


def load_env():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


PROFILES = {"pumui": "studio", "gyeolui": "gyeolui_studio"}  # 워크플로 종류 → rules.json runtime 칸
DEFAULT_ENV = {"key": "UPSTAGE_API_KEY", "agent_id": "AGENT_ID", "config_id": "CONFIG_ID"}


def profile(kind="pumui"):
    return rules()["runtime"].get(PROFILES[kind]) or {}


def settings(kind="pumui"):
    """(.env, rules.json)에서 API 주소·키·Agent ID·Config ID를 읽는다. 빠진 값 이름 목록도 돌려준다.

    품의(kind=pumui): UPSTAGE_API_KEY, AGENT_ID, CONFIG_ID
    결의(kind=gyeolui): GYEOLUI_API_KEY(비면 UPSTAGE_API_KEY), GYEOLUI_AGENT_ID, GYEOLUI_CONFIG_ID
    둘 다 rules.json에 agent_id가 있으면 그것을 먼저 쓴다.
    """
    global BASE
    env = load_env()
    BASE = env.get("UPSTAGE_BASE_URL") or rules()["runtime"]["api_base_url"]
    st = profile(kind)
    names = {**DEFAULT_ENV, **(st.get("env") or {})}
    key = env.get(names["key"]) or env.get(DEFAULT_ENV["key"])
    if st.get("agent_id"):  # 노드 워크플로가 정해져 있으면 그것을 쓴다
        s = {"key": key, "agent_id": st["agent_id"], "config_id": st.get("config_id")}
    else:
        s = {"key": key, "agent_id": env.get(names["agent_id"]), "config_id": env.get(names["config_id"], "1")}
    missing = [n for n, k in ((DEFAULT_ENV["key"], "key"), (names["agent_id"], "agent_id")) if not s[k]]
    return s, missing


def run_file(path, out_dir, progress=None, kind="pumui", on_job=None):
    """문서 하나를 Studio 워크플로(kind)로 돌려 out_dir/<이름>.json에 저장하고 (결과, job_id)를 돌려준다.
    progress(done, found, finished)는 품의 견적서 업로드 진행 표시용, on_job(job_id)는 실행이 접수되면 한 번 불린다."""
    s, missing = settings(kind)
    if missing:
        raise RuntimeError(f"{ROOT / '.env'}에 {', '.join(missing)} 값이 없어요.")
    result, job_id = run_one(Path(path), s["key"], s["agent_id"], s["config_id"], progress, kind, on_job)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{Path(path).stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, job_id


class StudioError(RuntimeError):
    """Studio API가 오류 코드를 돌려줬다. code=HTTP 상태, kind=분류(not_found/bad_key/quota/other)."""

    def __init__(self, code, detail, message):
        super().__init__(message)
        self.code, self.detail = code, detail
        low = detail.lower()
        self.kind = ("quota" if code == 403 and "usage_limit" in low else
                     "bad_key" if code == 401 else
                     "not_found" if code == 404 else "other")


def call(method, path, key, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise StudioError(e.code, detail, f"{method} {path} → HTTP {e.code}: {detail}") from None


def upload(path, key):
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nuser_data\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
         f"Content-Type: {ctype}\r\n\r\n").encode("utf-8") + path.read_bytes() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    res = call("POST", "/files", key, b"".join(parts),
               {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return res["id"]


def output_text(res):
    if res.get("output_text"):
        return res["output_text"]
    texts = [c.get("text", "") for o in res.get("output") or [] for c in o.get("content") or []
             if c.get("type") == "output_text"]
    return "".join(texts)


class NotQuote(RuntimeError):
    """Studio 분류 단계가 견적서가 아니라고 판단함."""

    def __init__(self, category):
        super().__init__(category)
        self.category = category


def stage_of(step_type, st):
    """Studio 단계 종류 → 화면 단계 이름(parse/classify/extract/instruct)."""
    types = st.get("step_types") or {}
    for stage, t in types.items():
        if step_type == t:
            return stage
    return "parse" if "parse" in (step_type or "") else None


def kind_of(text, st):
    """견적 검토 결과에서 '품목 분류:' 줄을 꺼낸다(없으면 빈 문자열)."""
    m = re.search(st.get("kind_pattern") or "$^", text or "", re.M)
    return re.sub(st.get("citation_pattern") or "$^", "", m.group(1)).strip() if m else ""


def partial(job, st):
    """진행 중인 작업에서 지금까지 끝난 단계와 읽어 낸 값을 뽑는다(화면 진행 표시용)."""
    steps = [o for o in job.get("output") or [] if o.get("step_type")]
    done, found = set(), {}
    types = st.get("step_types") or {}
    for o in steps:
        stage = stage_of(o["step_type"], st)
        if not stage or o.get("status", "completed") != "completed":  # 진행 중인 단계는 아직 비어 있다
            continue
        if stage != "instruct":  # 검토·판정은 Instruct 노드가 여러 개라 작업이 끝나야 끝난 것이다
            done.add(stage)
        text = step_text(o)
        if stage == "classify" and text:
            found["category"] = text.strip()
        if stage == "extract" and text:
            try:
                r = json.loads(text)
                F = fields()
                found.update({"vendor": r.get(F["vendor_name"]), "total": r.get(F["total"]),
                              "items": len(r.get(F["items"]) or [])})
            except (json.JSONDecodeError, KeyError):
                pass
        if stage == "instruct":
            if (o.get("model") or "").removeprefix(st.get("instruct_name_prefix", "")) == st.get("review_step") and kind_of(text, st):
                found["kind"] = kind_of(text, st)
            extra = (o.get("content") or [{}])[0].get("additional_values")
            extra = json.loads(extra) if isinstance(extra, str) and extra.startswith("{") else (extra or {})
            if extra.get("enum"):
                found["decision"] = extra["enum"]
    if job.get("status") == "completed":
        done.add("instruct")
    order = ["parse", "classify", "extract", "instruct"]  # 뒤 단계가 끝났으면 앞 단계도 끝난 것이다
    for i, s in enumerate(order):
        if any(x in done for x in order[i + 1:]):
            done.add(s)
    return sorted(done, key=order.index), found


def run_one(path, key, agent_id, config_id, progress=None, kind="pumui", on_job=None):
    """progress(done, found, finished)를 주면 단계가 끝날 때마다 알려 준다(화면 진행 표시용)."""
    st = profile(kind)
    include = st.get("include") or ["last"]
    poll_include = st.get("poll_include") or include
    tell = progress or (lambda *a: None)
    tell([], {}, False)
    file_id = upload(path, key)
    tell(["upload"], {}, False)
    req = {"model": agent_id, "include": include,
           "input": [{"role": "user", "content": [{"type": "input_file", "file_id": file_id}]}]}
    if config_id:
        req["config_id"] = config_id
    job = call("POST", "/responses", key, json.dumps(req).encode(), {"Content-Type": "application/json"})
    job_id = job["id"]
    if on_job:
        on_job(job_id)
    started = time.time()
    rt = rules()["runtime"]
    timeout = st.get("api_timeout_seconds") or rt["api_timeout_seconds"]
    query = lambda inc: "&".join(f"include[]={x}" for x in inc)
    while job.get("status") in ("queued", "in_progress", None):
        if time.time() - started > timeout:
            raise RuntimeError(f"{job_id}: {timeout}초 넘게 끝나지 않음")
        time.sleep(rt["api_poll_seconds"])
        job = call("GET", f"/responses/{job_id}?{query(poll_include)}", key)
        if progress:  # 품의 견적서 업로드 화면만 단계별 진행을 보여 준다
            done, found = partial(job, st)
            tell(["upload"] + done, found, False)
    if job.get("status") != "completed":
        raise RuntimeError(f"{job_id}: status={job.get('status')}")
    if poll_include != include:  # 끝난 뒤 단계별 결과를 한 번만 받는다
        job = call("GET", f"/responses/{job_id}?{query(include)}", key)
    steps = [o for o in job.get("output") or [] if o.get("step_type")]
    if progress:
        done, found = partial(job, st)
        tell(["upload"] + done, found, True)
    if kind == "gyeolui":
        from gyeolui import from_steps as gyeolui_steps
        return gyeolui_steps(steps, st, job_id), job_id
    if steps:
        return from_steps(steps, st, job_id), job_id
    text = output_text(job)
    try:
        return json.loads(text), job_id
    except json.JSONDecodeError:
        raise RuntimeError(f"{job_id}: output_text가 JSON이 아님: {text[:200]}") from None


def step_text(o):
    return "".join(c.get("text", "") for c in o.get("content") or [] if c.get("type") == "output_text")


def from_steps(steps, st, job_id):
    """노드 워크플로의 단계별 결과 → 추출 JSON + studio 칸."""
    types = st["step_types"]
    clean = lambda s: re.sub(st["citation_pattern"], "", s).strip()

    def unquote(s):  # Instruct 결과는 JSON 문자열로 감싸여 올 때가 있다
        try:
            v = json.loads(s)
            return v if isinstance(v, str) else s
        except json.JSONDecodeError:
            return s

    category = next((step_text(o) for o in steps if o["step_type"] == types["classify"]), "")
    ext = next((o for o in steps if o["step_type"] == types["extract"]), None)
    if ext is None:
        raise NotQuote(category)
    result = json.loads(step_text(ext))
    meta = {"category": category, "review": "", "kind": "", "decision": "", "branch": "", "note": ""}
    for o in steps:
        if o["step_type"] != types["instruct"]:
            continue
        name = (o.get("model") or "").removeprefix(st["instruct_name_prefix"])
        text = clean(unquote(step_text(o)))
        extra = (o.get("content") or [{}])[0].get("additional_values")
        extra = json.loads(extra) if isinstance(extra, str) and extra.startswith("{") else (extra or {})
        if extra.get("enum"):
            meta["decision"] = extra["enum"]
        elif name == st["review_step"]:
            meta["review"] = text
            meta["kind"] = kind_of(text, st)
        else:
            meta["branch"], meta["note"] = name, text
    meta["job_id"] = job_id
    result["studio"] = meta
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="문서 파일 또는 폴더")
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--score", action="store_true", help="실행 후 answers/와 채점")
    ap.add_argument("--skip-existing", action="store_true", help="이미 끝난 문서는 건너뛴다(노드 워크플로면 Studio 판정이 있는 결과만)")
    ap.add_argument("--workers", type=int, help="동시에 돌릴 문서 수(기본: rules.json runtime.studio.workers)")
    ap.add_argument("--kind", choices=sorted(PROFILES), default="pumui", help="pumui=견적서→품의, gyeolui=증빙 묶음→결의")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    s, missing = settings(a.kind)
    if missing:
        sys.exit(f"{ROOT / '.env'}에 {', '.join(missing)} 값을 넣어 주세요.")
    key, agent_id, config_id = s["key"], s["agent_id"], s["config_id"]

    target = Path(a.target)
    files = sorted(p for p in target.iterdir() if p.suffix.lower() in DOC_EXT) if target.is_dir() else [target]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Agent {agent_id} (config {config_id}) · 문서 {len(files)}건")

    if a.skip_existing:  # 노드 워크플로면 Studio 판정("studio")까지 있는 결과만 끝난 것으로 본다
        node = bool(profile(a.kind).get("agent_id"))

        def done(f):
            p = out / f"{f.stem}.json"
            return p.exists() and (not node or "studio" in json.loads(p.read_text(encoding="utf-8")))
        files = [f for f in files if not done(f)]
    workers = a.workers or profile(a.kind).get("workers", 1)
    print(f"실행할 문서 {len(files)}건, 동시 {workers}건")

    failed = []

    def one(f):
        t = time.time()
        try:
            result, job_id = run_one(f, key, agent_id, config_id, kind=a.kind)
        except NotQuote as e:
            return f, f"견적서 아님({e.category})", None
        except Exception as e:  # 한 건이 실패해도 나머지는 계속 돌린다
            return f, f"실패: {e}", None
        (out / f"{f.stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        st = result.get("studio") or {}
        decision = st.get("decision") or " / ".join((st.get("decisions") or {}).values())
        return f, f"완료 {time.time() - t:.0f}초  {decision}  ({job_id})", True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (f, msg, ok) in enumerate(pool.map(one, files), 1):
            print(f"[{i}/{len(files)}] {f.name}  {msg}", flush=True)
            if not ok:
                failed.append(f.name)

    print(f"\n완료 {len(files) - len(failed)}건 / 실패 {len(failed)}건")
    if a.score and a.kind == "pumui" and len(files) > len(failed):
        print()
        subprocess.run([sys.executable, str(ROOT / "tools" / "score.py"), str(out), str(ROOT / "answers"), "--check"])
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
