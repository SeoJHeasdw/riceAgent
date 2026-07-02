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

import asyncio
import logging

from .composition_root import Container, build_container

log = logging.getLogger("rice.server")


def create_app(container: Container):
    from contextlib import asynccontextmanager

    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse

    from ..core.errors import RiceError
    from .routes import connectors as connectors_routes
    from .routes import messages as messages_routes
    from .routes import plugins as plugins_routes
    from .routes import settings as settings_routes
    from .routes import skills as skills_routes
    from .routes import workspace as workspace_routes
    from .scheduler import CuratorScheduler

    # 백그라운드 curator: 앱 수명주기에 묶어 시작/정리한다 (RUNTIME_DESIGN §3).
    scheduler = CuratorScheduler(container)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler.start()
        # 로컬 MCP(.mcp.json, stdio) 서버를 spawn 해 도구 카탈로그를 미리 채운다.
        # 로컬 프로세스 실행이라 원격과 달리 부팅 시 수행해도 무방(원격은 여전히 부팅 시 X).
        # **백그라운드 태스크로** 돌린다 — lifespan 에서 await 하면 서빙 자체가 warm 완료를
        # 기다리는데, 죽은 stdio 서버가 있으면 타임아웃(~20초)까지 /health 도 응답하지 못해
        # supervisor(Electron)가 런타임을 오프라인으로 오판한다. warm 이 끝나기 전의
        # list_tools() 는 로컬 도구만 잠시 비어 있을 뿐이라(원격 카탈로그는 DB) 무해하다.
        warm = getattr(container.connectors, "warm_local", None)
        warm_task: asyncio.Task | None = None
        if warm is not None:
            async def _warm_in_background() -> None:
                try:
                    await warm()
                except Exception:  # noqa: BLE001 — 로컬 MCP warm 실패는 기동을 막지 않는다
                    log.warning("local mcp warm failed", exc_info=True)

            warm_task = asyncio.create_task(_warm_in_background())
        try:
            yield
        finally:
            if warm_task is not None and not warm_task.done():
                warm_task.cancel()
            await scheduler.stop()

    app = FastAPI(title="rice-runtime", version="0.1.0", lifespan=lifespan)
    app.state.container = container

    # Public paths that bypass the bearer-token guard:
    #  - /health: liveness probe (supervisor).
    #  - /connectors/oauth/callback: the OAuth redirect is hit by the *system
    #    browser*, which carries no Authorization header (CONTRACTS §4.2). The
    #    CSRF state token gates it instead.
    _PUBLIC_PATHS = frozenset({"/health", "/connectors/oauth/callback"})

    # ── auth guard (CONTRACTS §1.1) ───────────────────────────────────────────
    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        token = container.args.token
        # CORS preflight (OPTIONS) carries no Authorization header by design — let
        # it through so CORSMiddleware can answer it; real requests still gated.
        if token and request.method != "OPTIONS" and request.url.path not in _PUBLIC_PATHS:
            header = request.headers.get("authorization", "")
            if header != f"Bearer {token}":
                return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)
        return await call_next(request)

    # ── CORS (renderer-only dev) ──────────────────────────────────────────────
    # The Electron build talks to the runtime over IPC (same origin), so this is a
    # no-op there. But `vite.renderer.config.ts` runs the React app in a plain
    # browser (http://localhost:5273) that fetches this localhost API cross-origin,
    # which the browser blocks without these headers. Scoped to localhost only;
    # added last so it sits OUTERMOST and answers preflight before the auth guard.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── uniform error envelope ────────────────────────────────────────────────
    @app.exception_handler(RiceError)
    async def rice_error_handler(_request: Request, exc: RiceError):
        return JSONResponse({"success": False, "error": exc.message, "code": exc.code}, status_code=exc.http)

    # ── lifecycle ─────────────────────────────────────────────────────────────
    @app.get("/health")
    async def health():
        cfg = container.config.provider
        return {
            "status": "ok",
            "provider": container.resolve_provider().name,
            "model": cfg.model,
            "version": "0.1.0",
        }

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
    app.include_router(workspace_routes.router)
    return app


def serve(host: str | None = None, port: int | None = None, token: str = "") -> None:
    """Build the container + app and run uvicorn. Used by `__main__`."""
    import uvicorn

    from ..core.config import RuntimeArgs

    # 실제 바인딩 포트를 먼저 확정한 뒤 컨테이너에 주입한다 — OAuth redirect_uri
    # (http://localhost:{port}/connectors/oauth/callback)가 바인딩 포트와 일치해야 한다.
    bind_port = port or 8080
    container = build_container(RuntimeArgs(host=host or "127.0.0.1", port=bind_port, token=token))
    app = create_app(container)
    log.info("rice-runtime listening on %s:%s", container.args.host, bind_port)
    uvicorn.run(app, host=container.args.host, port=bind_port, log_level="info")
