"""rice-runtime — RICE's local single-process brain layer (배포물 ②).

A modular monolith: 6 modules (server / agent / execution / learning / connector /
core) wired by ONE composition root, talking only through `interfaces.py` Protocols
(RICE_RUNTIME_DIRECTORY.md is the canonical module map).

Public surface:
  build_container(...)  → the wired object graph (no web deps required)
  create_app(container) → the FastAPI app (needs fastapi)
"""

from __future__ import annotations

from .server.composition_root import Container, build_container

__all__ = ["Container", "build_container"]
__version__ = "0.1.0"
