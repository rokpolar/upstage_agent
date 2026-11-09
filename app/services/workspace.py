"""공유 작업 수명, 원자적 변경과 외부 작업의 버전 확인."""

import copy
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from app.adapters.files import inside
from app.adapters.store import StateStore
from app.adapters.studio import StudioClient
from app.config import Settings
from app.domain import gyeolui
from app.domain.select_vendor import finalize, recommend
from app.models import Progress, Workspace

from .expenses import ExpensesService
from .forms import FormsService
from .quotes import QuotesService


class ConflictError(RuntimeError):
    pass


@dataclass
class Dependencies:
    studio: StudioClient
    store: StateStore


class Application(QuotesService, FormsService, ExpensesService):
    def __init__(self, settings=None, dependencies=None):
        self.settings = settings or Settings.load()
        self.root = self.settings.root
        self.cfg = self.settings.ui
        self.gcfg = self.cfg["gyeolui_sets"]
        self.out = self.root / self.cfg["output_dir"]
        deps = dependencies or Dependencies(
            StudioClient(self.root, self.settings.runtime),
            StateStore(self.settings.database),
        )
        self.studio, self.store = deps.studio, deps.store
        self.lock = threading.RLock()
        self.version = 0
        self.progress: dict[str, Progress] = {}
        self.threads = []
        self.state: Workspace = self.store.load() or {
            "case": json.loads(
                (self.root / self.cfg["default_case"]).read_text(encoding="utf-8")
            ),
            "quotes": {},
            "selection": None,
            "texts": {},
            "g": {},
            "handoff": None,
        }
        self.load_quotes()
        self.load_gyeolui()
        self.store.save(self.state)

    def execute(self, operation, *args, expected=None):
        """Persist first, acknowledge second; restore memory if any part fails."""
        with self.lock:
            if expected is not None and expected != self.version:
                raise ConflictError("작업 내용이 변경되었습니다. 다시 시도하세요.")
            before = copy.deepcopy(self.state)
            try:
                result = operation(*args)
                self.store.save(self.state)
            except BaseException:
                self.state = before
                raise
            self.version += 1
            return copy.deepcopy(result)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state), self.version

    def studio_missing(self, kind="pumui"):
        return self.studio.settings(kind)[1]

    def form_path(self, relative):
        if not isinstance(relative, str):
            raise ValueError("양식 경로가 필요합니다.")
        path = (self.root / relative).resolve()
        dirs = set(self.cfg["form_dirs"] + self.cfg["gyeolui_form_dirs"])
        if (
            path.suffix.lower() != ".hwpx"
            or not any(inside(path, self.root / d) for d in dirs)
            or not path.is_file()
        ):
            raise FileNotFoundError("양식을 찾지 못했습니다.")
        return path

    def source_path(self, gid):
        with self.lock:
            entry = self.state["g"].get(gid)
            if not entry or not entry["source"]:
                raise FileNotFoundError("not found")
            path = Path(entry["source"]).resolve()
            if (
                not any(inside(path, self.root / entry[1]) for entry in self.gcfg)
                or not path.is_file()
            ):
                raise FileNotFoundError("not found")
            return path

    def set_case(self, case):
        from app.config import config

        for key, *_ in config("ui")["case"]["fields"]:
            if key in case and not isinstance(case[key], str):
                raise ValueError(f"품의 정보 '{key}'는 문자열이어야 합니다.")
        self.state["case"] = case
        for qid in self.state["quotes"]:
            self.check(qid)
            self.state["quotes"][qid]["fill"] = None
        return {"quotes": [self.summary(i) for i in self.state["quotes"]]}

    def select(self, ids, pick, reason):
        quotes = [self.quote_for_select(i) for i in ids]
        rec, auto, _, _ = recommend(quotes)
        chosen = next((q for q in quotes if q["file"] == pick), None)
        if chosen is None:
            raise ValueError("선택한 견적이 비교 목록에 없습니다.")
        self.state["selection"] = finalize(
            quotes, rec, auto, chosen, reason.strip(), True
        )
        return self.state["selection"]

    def g_answer(self, gid, key, value, who):
        e = self.state["g"][gid]
        if e["status"] != "done":
            raise ConflictError("처리가 완료된 뒤 답변하세요.")
        gyeolui.answer(
            e["state"],
            key,
            value,
            who
            or e["state"]["info"].get("drafter")
            or self.state["case"].get("drafter", ""),
        )
        e["fill"] = None
        return self.g_summary(gid)

    def g_info(self, gid, info):
        e = self.state["g"][gid]
        if e["status"] != "done":
            raise ConflictError("처리가 완료된 뒤 저장하세요.")
        if any(not isinstance(v, str) for v in info.values()):
            raise ValueError("결의 정보는 문자열이어야 합니다.")
        e["state"]["info"] = {k: v for k, v in info.items() if v.strip()}
        e["info_saved"] = True
        e["fill"] = None
        return self.g_summary(gid)

    def close(self):
        for thread in self.threads:
            thread.join(timeout=1)
