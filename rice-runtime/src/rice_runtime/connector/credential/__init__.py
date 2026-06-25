"""credential/ — OS 키체인 기반 토큰 저장 (RUNTIME_DESIGN §9.3).

토큰은 **절대** DB·로그·반환 JSON 에 저장하거나 노출하지 않는다.
"""
from __future__ import annotations

from .keychain import KeychainStore

__all__ = ["KeychainStore"]
