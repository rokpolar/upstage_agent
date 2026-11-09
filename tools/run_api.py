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
import mimetypes
import os
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

BASE = None  # settings()에서 환경변수·.env 또는 rules.json으로 정한다


def limits():
    """API 입력 제한(rules.json runtime.limits): 지원 형식, 파일 크기, 쪽 수."""
    return rules()["runtime"]["limits"]


DOC_EXT = set(limits()["formats"])  # 폴더를 돌릴 때 고르는 확장자 = API 지원 형식


class InputError(ValueError):
    """올리기 전에 API 입력 제한(형식·크기·쪽 수)에 걸렸다."""


def check_input(path):
    """API 명세의 입력 제한을 미리 검사한다. 걸리면 InputError(화면에 그대로 보여 줄 문장)."""
    lim, path = limits(), Path(path)
    msg = lim["messages"]
    if path.suffix.lower() not in lim["formats"]:
        raise InputError(msg["format"].format(ext=path.suffix or "?", formats=", ".join(sorted(lim["formats"]))))
    size = path.stat().st_size
    if size > lim["max_file_bytes"]:
        raise InputError(msg["size"].format(mb=size // 1_000_000, max_mb=lim["max_file_bytes"] // 1_000_000))
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            pages = len(PdfReader(str(path)).pages)
        except Exception:  # 쪽 수를 못 세면 Studio에 맡긴다
            pages = 0
        if pages > lim["max_pages"]:
            raise InputError(msg["pages"].format(pages=pages, max_pages=lim["max_pages"]))


def load_env():
    """.env를 읽고, 같은 이름의 환경변수가 있으면 그 값을 쓴다(빈 값은 없는 것으로 본다)."""
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if v and (k.startswith(("UPSTAGE_", "PUMUI_", "GYEOLUI_"))
                                                               or k in ("AGENT_ID", "CONFIG_ID"))})
    return {k: v for k, v in env.items() if v}


PROFILES = {"pumui": "studio", "gyeolui": "gyeolui_studio"}  # 워크플로 종류 → rules.json runtime 칸


def profile(kind="pumui"):
    return rules()["runtime"].get(PROFILES[kind]) or {}


def settings(kind="pumui"):
    """워크플로(kind)의 API 키·Agent ID·Config ID·Project ID를 정한다. 빠진 값 이름 목록도 돌려준다.

    워크플로마다 환경변수(rules.json runtime.<워크플로>.env의 이름) > .env > rules.json 기본값 순으로 쓴다.
    품의와 결의는 Agent가 다르므로 AGENT_ID 하나를 같이 쓰지 않는다.
    """
    global BASE
    env = load_env()
    BASE = env.get("UPSTAGE_BASE_URL") or rules()["runtime"]["api_base_url"]
    st = profile(kind)
    names = st["env"]
    first = lambda field: next((env[n] for n in names[field] if env.get(n)), None)
    config_id = first("config_id") or st.get("config_id")
    s = {"key": first("key"), "agent_id": first("agent_id") or st.get("agent_id"),
         "config_id": None if str(config_id).lower() in ("latest", "none", "") else str(config_id),
         "project_id": first("project_id") or st.get("project_id")}
    missing = ([names["key"][-1]] if not s["key"] else []) + ([names["agent_id"][0]] if not s["agent_id"] else [])
    return s, missing


def run_file(path, out_dir, progress=None, kind="pumui", on_job=None):
    """문서 하나를 Studio 워크플로(kind)로 돌려 out_dir/<이름>.json에 저장하고 (결과, job_id)를 돌려준다.
    progress(done, found, finished)는 품의 견적서 업로드 진행 표시용, on_job(job_id)는 실행이 접수되면 한 번 불린다."""
    s, missing = settings(kind)
    if missing:
        raise RuntimeError(f"{ROOT / '.env'}에 {', '.join(missing)} 값이 없어요.")
    result, job_id = run_one(Path(path), s["key"], s["agent_id"], s["config_id"], progress, kind, on_job, s["project_id"])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{Path(path).stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, job_id


class StudioError(RuntimeError):
    """Studio API가 오류 코드를 돌려줬다. code=HTTP 상태,
    kind=분류(bad_key 401 / quota 403 사용량 / project_forbidden 403 프로젝트 권한 / not_found 404 / too_large 413 / rate_limit 429 / server 5xx / other)."""

    def __init__(self, code, detail, message):
        super().__init__(message)
        self.code, self.detail = code, detail
        low = detail.lower()
        self.kind = ("quota" if code == 403 and "usage_limit" in low else
                     "project_forbidden" if code == 403 and "project" in low else
                     "bad_key" if code == 401 else
                     "not_found" if code == 404 else
                     "too_large" if code == 413 else
                     "rate_limit" if code == 429 else
                     "server" if code >= 500 else "other")


def call(method, path, key, body=None, headers=None, project=None):
    """API 한 번 호출. 429·5xx는 rules.json runtime.retry대로 기다렸다 다시 보낸다(429는 Retry-After를 따른다).
    조회(GET)는 연결 오류도 다시 시도한다. 실행 생성(POST)의 연결 오류는 이미 접수됐을 수 있어 다시 보내지 않는다."""
    rt = rules()["runtime"]["retry"]
    for attempt in range(rt["attempts"]):
        req = urllib.request.Request(BASE + path, data=body, method=method)
        req.add_header("Authorization", f"Bearer {key}")
        if project:
            req.add_header("x-project-id", project)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        last = attempt == rt["attempts"] - 1
        wait = min(rt["max_seconds"], rt["base_seconds"] * 2 ** attempt)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if last or not (e.code == 429 or e.code >= 500):
                raise StudioError(e.code, detail, f"{method} {path} → HTTP {e.code}: {detail}") from None
            after = e.headers.get("Retry-After") if e.headers else None
            if after and after.isdigit():
                wait = max(wait, min(rt["max_seconds"], int(after)))
        except (urllib.error.URLError, TimeoutError):
            if last or method != "GET":
                raise
        time.sleep(wait)


def upload(path, key, project=None):
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nuser_data\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
         f"Content-Type: {ctype}\r\n\r\n").encode("utf-8") + path.read_bytes() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    res = call("POST", "/files", key, b"".join(parts),
               {"Content-Type": f"multipart/form-data; boundary={boundary}"}, project)
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


def run_one(path, key, agent_id, config_id, progress=None, kind="pumui", on_job=None, project=None):
    """progress(done, found, finished)를 주면 단계가 끝날 때마다 알려 준다(화면 진행 표시용).
    project가 있으면 업로드·실행·조회 모두에 x-project-id를 보낸다."""
    check_input(path)
    st = profile(kind)
    include = st.get("include") or ["last"]
    poll_include = st.get("poll_include") or include
    tell = progress or (lambda *a: None)
    tell([], {}, False)
    file_id = upload(path, key, project)
    tell(["upload"], {}, False)
    req = {"model": agent_id, "include": include,
           "input": [{"role": "user", "content": [{"type": "input_file", "file_id": file_id}]}]}
    if config_id:
        req["config_id"] = config_id
    job = call("POST", "/responses", key, json.dumps(req).encode(), {"Content-Type": "application/json"}, project)
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
        job = call("GET", f"/responses/{job_id}?{query(poll_include)}", key, project=project)
        if progress:  # 품의 견적서 업로드 화면만 단계별 진행을 보여 준다
            done, found = partial(job, st)
            tell(["upload"] + done, found, False)
    if job.get("status") != "completed":
        raise RuntimeError(f"{job_id}: status={job.get('status')} (failed면 Studio에서 Agent 설정 확인)")
    if poll_include != include:  # 끝난 뒤 단계별 결과를 한 번만 받는다
        job = call("GET", f"/responses/{job_id}?{query(include)}", key, project=project)
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
    key, agent_id, config_id, project = s["key"], s["agent_id"], s["config_id"], s["project_id"]

    target = Path(a.target)
    files = sorted(p for p in target.iterdir() if p.suffix.lower() in DOC_EXT) if target.is_dir() else [target]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Agent {agent_id} (config {config_id or 'latest'}, project {project or '-'}) · 문서 {len(files)}건")

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
            result, job_id = run_one(f, key, agent_id, config_id, kind=a.kind, project=project)
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
