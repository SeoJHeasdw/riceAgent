"""Error taxonomy for rice-runtime.

A small, stable set of error types that map cleanly onto:
  - the SSE `error` event (`error_code`, `message`) — CONTRACTS §1.3, and
  - HTTP status codes at the route boundary.

Modules raise these; the server layer (routes / SSE emitter) is the only place that
translates them to wire responses. Keeping `code` stable lets the app branch on it.
"""

from __future__ import annotations


class RiceError(Exception):
    """Base for all runtime errors. `code` is the stable wire identifier; `http`
    is the suggested status when surfaced over the REST boundary."""

    code: str = "internal_error"
    http: int = 500

    def __init__(self, message: str = "", *, code: str | None = None, http: int | None = None):
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        if code is not None:
            self.code = code
        if http is not None:
            self.http = http

    def to_event_data(self) -> dict[str, str]:
        return {"error_code": self.code, "message": self.message}


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


class AbortedError(RiceError):
    """Turn cancelled via POST /sessions/{id}/abort."""

    code = "aborted"
    http = 409
