"""내부 상태 타입. 외부 Studio 키 이름은 설정 파일을 따른다."""

from typing import Any, NotRequired, TypedDict


class ReviewState(TypedDict):
    result: dict[str, Any]
    questions: list[dict[str, Any]]
    resolution_log: list[dict[str, Any]]
    acknowledged: list[str]
    hold: bool
    source: NotRequired[str | None]
    info: NotRequired[dict[str, str]]
    hints: NotRequired[dict[str, str]]


class Progress(TypedDict):
    done: list[str]
    found: dict[str, Any]
    finished: bool


class QuoteEntry(TypedDict):
    set: int
    name: str
    file: str
    source: str | None
    state: ReviewState
    fill: NotRequired[dict[str, Any] | None]


class ExpenseEntry(TypedDict):
    id: str
    set: int
    name: str
    status: str
    source: str | None
    state: ReviewState | None
    info_saved: bool
    fill: dict[str, Any] | None
    error: str | None
    job_id: str | None
    started: float | None
    finished: float | None
    link: dict[str, Any] | None
    parts: NotRequired[list[str]]
    progress: NotRequired[Progress | None]


class Workspace(TypedDict):
    case: dict[str, Any]
    quotes: dict[str, QuoteEntry]
    selection: dict[str, Any] | None
    texts: dict[str | None, str]
    g: dict[str, ExpenseEntry]
    handoff: dict[str, Any] | None
