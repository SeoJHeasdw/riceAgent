"""permission/ — Plugin → Connector 접근 권한 검증 (RUNTIME_DESIGN §9.4, CONTRACTS §4.4)."""
from __future__ import annotations

from .checker import PermissionChecker

__all__ = ["PermissionChecker"]
