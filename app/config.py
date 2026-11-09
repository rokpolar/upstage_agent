import copy
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


@lru_cache(maxsize=None)
def config(name):
    return json.loads((CONFIG / f"{name}.json").read_text(encoding="utf-8"))


def rules():
    return config("rules")


def fields():
    return config("fields")


def categories():
    return config("categories")["categories"]


def find_pattern(name, text):
    """rules.json patterns[name]의 정규식 중 처음 맞는 것의 match (없으면 None)."""
    for p in rules()["patterns"][name]:
        m = re.search(p, text)
        if m:
            return m
    return None


@dataclass(frozen=True)
class Settings:
    root: Path
    ui: dict
    runtime: dict
    database: Path

    @classmethod
    def load(cls, root=ROOT):
        root = Path(root).resolve()
        for path in (root / "config").glob("*.json"):
            if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                raise ValueError(f"설정 {path.name}은 JSON 객체여야 합니다.")
        cfg = json.loads((root / "config/rules.json").read_text(encoding="utf-8"))
        ui, runtime = cfg["ui"], cfg["runtime"]
        for key in ("quote_sets", "gyeolui_sets", "form_dirs", "gyeolui_form_dirs"):
            if not isinstance(ui.get(key), list) or not ui[key]:
                raise ValueError(f"설정 ui.{key}가 비어 있습니다.")
        for key in ("output_dir", "upload_dir", "default_case"):
            if not isinstance(ui.get(key), str) or not (
                root / ui[key]
            ).resolve().is_relative_to(root):
                raise ValueError(f"설정 ui.{key} 경로가 올바르지 않습니다.")
        for entries in (ui["quote_sets"], ui["gyeolui_sets"]):
            for entry in entries:
                if len(entry) != 3 or any(
                    not (root / p).resolve().is_relative_to(root) for p in entry[:2]
                ):
                    raise ValueError("결과·원본 폴더 설정이 올바르지 않습니다.")
        for key in ("form_dirs", "gyeolui_form_dirs"):
            if any(not (root / p).resolve().is_relative_to(root) for p in ui[key]):
                raise ValueError("양식 폴더 설정이 올바르지 않습니다.")
        if not isinstance(ui["port"], int) or not 0 < ui["port"] < 65536:
            raise ValueError("서버 포트 설정이 올바르지 않습니다.")
        if runtime["retry"]["attempts"] < 1 or runtime["api_poll_seconds"] <= 0:
            raise ValueError("API 재시도·조회 설정이 올바르지 않습니다.")
        for name in ("studio", "gyeolui_studio"):
            profile = runtime[name]
            for field in ("key", "agent_id", "config_id", "project_id"):
                if (
                    not isinstance(profile["env"][field], list)
                    or not profile["env"][field]
                ):
                    raise ValueError(
                        f"설정 runtime.{name}.env.{field}가 올바르지 않습니다."
                    )
        return cls(
            root, copy.deepcopy(ui), copy.deepcopy(runtime), root / "out/state.sqlite3"
        )
