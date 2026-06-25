"""Shared route dependency: pull the wired `Container` off app state.

Routes depend on this, never on `build_container` — so they receive the same
single object graph the composition root built (RUNTIME_DIRECTORY invariant #2).
"""

from __future__ import annotations

from fastapi import Request

from ..composition_root import Container


def get_container(request: Request) -> Container:
    return request.app.state.container
