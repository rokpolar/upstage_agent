class InputError(ValueError):
    """올리기 전에 API 입력 제한(형식·크기·쪽 수)에 걸렸다."""


class StudioError(RuntimeError):
    """Studio API가 오류 코드를 돌려줬다. code=HTTP 상태,
    kind=분류(bad_key 401 / quota 403 사용량 / project_forbidden 403 프로젝트 권한 / not_found 404 / too_large 413 / rate_limit 429 / server 5xx / other)."""

    def __init__(self, code, detail, message):
        super().__init__(message)
        self.code, self.detail = code, detail
        low = detail.lower()
        self.kind = (
            "quota"
            if code == 403 and "usage_limit" in low
            else "project_forbidden"
            if code == 403 and "project" in low
            else "bad_key"
            if code == 401
            else "not_found"
            if code == 404
            else "too_large"
            if code == 413
            else "rate_limit"
            if code == 429
            else "server"
            if code >= 500
            else "other"
        )


class NotQuote(RuntimeError):
    """Studio 분류 단계가 견적서가 아니라고 판단함."""

    def __init__(self, category):
        super().__init__(category)
        self.category = category
