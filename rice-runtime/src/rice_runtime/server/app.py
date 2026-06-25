"""FastAPI application factory + lifecycle (#13, CONTRACTS §1.1).

`create_app(container)` builds the ASGI app, wires the bearer-token guard, mounts
the routers, and exposes the lifecycle endpoints (`/health`, `/shutdown`). The app
holds the `Container` in `app.state.container`; routes read services off it via the
`get_container` dependency. Nothing here constructs a service — that's the
composition root's sole job.

Security baseline (RUNTIME_DESIGN §10.1): when a one-shot token is present (the
Electron supervisor injects one on spawn), every request except `/health` must
carry `Authorization: Bearer <token>`. Binds localhost only.

FastAPI/uvicorn are imported lazily inside `create_app`/`serve` so importing the
package (and running the stdlib walking-skeleton demo) needs no web deps.
"""

from __future__ import annotations

import logging

from .composition_root import Container, build_container

log = logging.getLogger("rice.server")


def create_app(container: Container):
    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse

    from ..core.errors import RiceError
    from .routes import connectors as connectors_routes
    from .routes import messages as messages_routes
    from .routes import plugins as plugins_routes
    from .routes import settings as settings_routes
    from .routes import skills as skills_routes

    app = FastAPI(title="rice-runtime", version="0.1.0")
    app.state.container = container

    # ── auth guard (CONTRACTS §1.1) ───────────────────────────────────────────
    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        token = container.args.token
        if token and request.url.path != "/health":
            header = request.headers.get("authorization", "")
            if header != f"Bearer {token}":
                return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)
        return await call_next(request)

    # ── uniform error envelope ────────────────────────────────────────────────
    @app.exception_handler(RiceError)
    async def rice_error_handler(_request: Request, exc: RiceError):
        return JSONResponse({"success": False, "error": exc.message, "code": exc.code}, status_code=exc.http)

    # ── lifecycle ─────────────────────────────────────────────────────────────
    @app.get("/health")
    async def health():
        return {"status": "ok", "provider": container.resolve_provider().name, "version": "0.1.0"}

    @app.post("/shutdown")
    async def shutdown():
        # Graceful stop is the supervisor's call; we tear down resources here.
        container.shutdown()
        return {"success": True}

    # ── routers (one per CONTRACTS surface) ───────────────────────────────────
    app.include_router(messages_routes.router)
    app.include_router(skills_routes.router)
    app.include_router(plugins_routes.router)
    app.include_router(connectors_routes.router)
    app.include_router(settings_routes.router)
    return app


def serve(host: str | None = None, port: int | None = None, token: str = "") -> None:
    """Build the container + app and run uvicorn. Used by `__main__`."""
    import uvicorn

    from ..core.config import RuntimeArgs

    container = build_container(RuntimeArgs(host=host or "127.0.0.1", port=port or 0, token=token))
    app = create_app(container)
    bind_port = container.args.port or 8080
    log.info("rice-runtime listening on %s:%s", container.args.host, bind_port)
    uvicorn.run(app, host=container.args.host, port=bind_port, log_level="info")
