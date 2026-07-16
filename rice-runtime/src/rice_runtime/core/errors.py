"""Error taxonomy for rice-runtime.

A small, stable set of error types that map cleanly onto:
  - the SSE `error` event (`error_code`, `message`) — CONTRACTS §1.3, and
  - HTTP status codes at the route boundary.

Modules raise these; the server layer (routes / SSE emitter) is the only place that
translates them to wire responses. Keeping `code` stable lets the app branch on it.
"""

from __future__ import annotations

import re


class RiceError(Exception):
    """Base for all runtime errors. `code` is the stable wire identifier; `http`
    is the suggested status when surfaced over the REST boundary."""

    code: str = "internal_error"
    http: int = 500

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        http: int | None = None,
        params: dict[str, object] | None = None,
    ):
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        if code is not None:
            self.code = code
        if http is not None:
            self.http = http
        # `params` 는 UI 문구 보간용 값(예: {"detail": ..., "baseUrl": ...}).
        # 런타임은 자연어 문장을 만들지 않는다 — 안정적인 `code` + `params` 만 내려주고
        # 실제 표시 문구는 프론트의 errors 카탈로그가 담당한다.
        self.params: dict[str, object] = dict(params) if params else {}

    def to_event_data(self) -> dict[str, object]:
        return {"error_code": self.code, "message": self.message, "params": self.params}


class NotFoundError(RiceError):
    code = "not_found"
    http = 404


class ValidationError(RiceError):
    code = "validation_error"
    http = 422


class ConflictError(RiceError):
    """Late/duplicate approval POST against an already-resolved approval_id →
    409/410 idempotent no-op (RUNTIME_DESIGN §5.1)."""

    code = "conflict"
    http = 409


class GoneError(RiceError):
    code = "gone"
    http = 410


class PermissionDeniedError(RiceError):
    """Plugin/tool tried to reach a connector it isn't granted (CONTRACTS §4.4)."""

    code = "permission_denied"
    http = 403


class ApprovalTimeoutError(RiceError):
    """HITL approval expired; safe default = reject/cancel (RUNTIME_DESIGN §5.1)."""

    code = "approval_timeout"
    http = 408


class ProviderError(RiceError):
    """LLM provider failure (network, auth, unsupported capability)."""

    code = "provider_error"
    http = 502


# 채팅 UI가 사용자의 다음 행동을 정확히 안내할 수 있는 provider 실패 코드.
# provider SDK/httpx 예외 타입은 어댑터마다 다르므로, 네트워크 경계에서 아래의
# 안정 코드로 정규화한 뒤 SSE까지 그대로 보존한다.
PROVIDER_OLLAMA_UNREACHABLE = "provider.ollama_unreachable"
PROVIDER_MODEL_NOT_FOUND = "provider.model_not_found"
PROVIDER_AUTH_FAILED = "provider.auth_failed"
PROVIDER_QUOTA_EXCEEDED = "provider.quota_exceeded"


def _provider_status_code(exc: BaseException) -> int | None:
    """SDK/httpx 예외에서 HTTP status를 라이브러리 결합 없이 꺼낸다."""

    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status

    match = re.search(r"(?<!\d)(401|402|403|404|429)(?!\d)", str(exc))
    return int(match.group(1)) if match else None


def classify_provider_error(exc: BaseException, provider: str) -> ProviderError:
    """Provider 경계의 원시 예외를 사용자 조치가 가능한 네 분류로 정규화한다.

    분류하지 못한 400/5xx/프로토콜 오류는 기존 ``provider_error``로 남긴다.
    메시지는 진단 로그용으로 보존하고 UI는 안정적인 ``code``만 사용한다.
    """

    if isinstance(exc, ProviderError) and exc.code != ProviderError.code:
        return exc

    status = _provider_status_code(exc)
    text = str(exc).lower()

    if status in {401, 403} or any(
        marker in text
        for marker in (
            "unauthorized",
            "forbidden",
            "authentication_error",
            "authentication failed",
            "invalid api key",
            "invalid_api_key",
            "incorrect api key",
        )
    ):
        code = PROVIDER_AUTH_FAILED
    elif status in {402, 429} or any(
        marker in text
        for marker in (
            "rate limit",
            "rate_limit",
            "too many requests",
            "insufficient_quota",
            "quota exceeded",
            "usage limit",
            "credit balance",
            "billing limit",
            "payment required",
        )
    ):
        code = PROVIDER_QUOTA_EXCEEDED
    elif status == 404 or re.search(
        r"\bmodel\b.{0,80}\b(?:not found|does not exist|was not found)\b", text
    ) or any(
        marker in text
        for marker in (
            "model not found",
            "model_not_found",
            "model does not exist",
            "model was not found",
            "no such model",
        )
    ):
        code = PROVIDER_MODEL_NOT_FOUND
    elif provider == "ollama" and (
        (status is not None and status >= 500)
        or any(
            marker in text
            for marker in (
                "connection refused",
                "connection reset",
                "connect error",
                "connection error",
                "network is unreachable",
                "name or service not known",
                "timed out",
                "timeout",
                "peer closed connection",
                "incomplete chunked read",
            )
        )
    ):
        code = PROVIDER_OLLAMA_UNREACHABLE
    else:
        code = ProviderError.code

    return ProviderError(f"{provider} request failed: {exc}", code=code)


class AbortedError(RiceError):
    """Turn cancelled via POST /sessions/{id}/abort."""

    code = "aborted"
    http = 409


# ── transient provider-failure detection ─────────────────────────────────────
# provider별 문구가 다르므로 흔한 표현을 보수적으로 모은다(is_context_length_error
# 와 같은 접근 — core/tokens.py). 매칭되면 루프가 같은 컨텍스트로 짧은 backoff 후
# 재시도한다: rate limit(429)·과부하(529/overloaded)·일시적 5xx·네트워크 단절은
# 요청을 다시 보내는 것만으로 대부분 회복된다. 인증(401)·권한(403)·유효성(400)
# 실패는 재시도해도 같은 결과라 여기 없다.
_TRANSIENT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "internal server error",
    "server had an error",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "connection error",
    "connect error",
    "peer closed connection",
    "incomplete chunked read",
)
# 상태 코드는 단어 경계로만 매칭 — "500"이 토큰 수("500123 tokens") 같은 무관한
# 숫자의 부분 문자열에 오탐되지 않게 한다.
_TRANSIENT_STATUS = re.compile(r"\b(?:429|500|502|503|504|529)\b")


def is_transient_provider_error(exc: BaseException) -> bool:
    """예외가 재시도로 회복 가능한 일시적 provider 장애(과부하·rate limit·5xx·
    네트워크 단절)인가 — 루프의 backoff 재시도 트리거. 컨텍스트 길이 초과는
    별개의 처리(더 강한 압축 재시도)라 여기서 다루지 않는다: 호출부는
    is_context_length_error 를 먼저 확인해야 한다."""
    text = str(exc).lower()
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return True
    return _TRANSIENT_STATUS.search(text) is not None
