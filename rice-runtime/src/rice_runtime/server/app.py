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
    from .routes import eval_impact as eval_impact_routes
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
            try:
                await scheduler.stop()
            finally:
                # Electron normally terminates the runtime with SIGTERM instead
                # of calling POST /shutdown. ASGI lifespan is therefore the
                # authoritative chance to unload Ollama and close resources.
                await container.shutdown_async()

    app = FastAPI(title="rice-runtime", version="0.1.0", lifespan=lifespan)
    app.state.container = container

    # Public paths that bypass the bearer-token guard:
    #  - /health: liveness probe (supervisor).
    #  - /connectors/oauth/callback: the OAuth redirect is hit by the *system
    #    browser*, which carries no Authorization header (CONTRACTS §4.2). The
    #    CSRF state token gates it instead.
    _PUBLIC_PATHS = frozenset({"/health", "/connectors/oauth/callback"})

    # 로컬 API 인접 공격 방어: localhost 바인딩 + bearer 토큰만으로는
    # 부족하다는 적대 리뷰 지적을 반영. 브라우저에 열린 임의 웹페이지(evil.com)가
    # 사용자의 localhost 런타임으로 요청을 보내는 시나리오를 Origin/Host 검증으로
    # 한 겹 더 막는다. 토큰이 없는 dev 모드에서도 이 검증은 항상 작동한다.
    def _host_is_local(value: str) -> bool:
        host = value.split("//")[-1].split("/")[0].split(":")[0].strip().lower()
        return host in ("localhost", "127.0.0.1", "[::1]", "::1", "")

    # ── auth guard (CONTRACTS §1.1 Origin/Host 방어) ────────────
    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        token = container.args.token
        if request.method != "OPTIONS" and request.url.path not in _PUBLIC_PATHS:
            # (1) Origin 방어: 브라우저가 붙인 Origin이 있고 localhost가 아니면 거부
            #     (cross-site 웹페이지의 localhost 공격 차단). Electron IPC·서버간
            #     호출은 Origin이 없으므로 통과. 로컬 브라우저 dev(5273)는 통과.
            origin = request.headers.get("origin")
            if origin and not _host_is_local(origin):
                return JSONResponse({"success": False, "error": "forbidden origin"}, status_code=403)
            # (2) Host 방어(DNS rebinding): Host 헤더가 localhost 계열이 아니면 거부.
            host_header = request.headers.get("host", "")
            if host_header and not _host_is_local(host_header):
                return JSONResponse({"success": False, "error": "forbidden host"}, status_code=403)
            # (3) bearer token: 토큰이 설정돼 있으면 모든 경로가 동일하게 통과해야 한다.
            if token:
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
        # 프로세스 종료까지 책임진다. uvicorn Server 가 있으면
        # `should_exit` 만 세운다 — 자원 정리는 ASGI lifespan 의 finally 가
        # (scheduler.stop → container.shutdown_async) 단일 경로로 수행하므로
        # 여기서 먼저 DB 를 닫아 "서빙 중인데 내부는 닫힌" 좀비를 만들지 않는다.
        # 서버 밖에서 띄운 테스트 앱(TestClient 등)은 종전대로 직접 정리한다.
        server = getattr(app.state, "uvicorn_server", None)
        if server is not None:
            server.should_exit = True
        else:
            await container.shutdown_async()
        return {"success": True}

    # ── routers (one per CONTRACTS surface) ───────────────────────────────────
    app.include_router(messages_routes.router)
    app.include_router(skills_routes.router)
    app.include_router(plugins_routes.router)
    app.include_router(connectors_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(workspace_routes.router)
    app.include_router(eval_impact_routes.router)
    return app


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def serve(
    host: str | None = None,
    port: int | None = None,
    token: str = "",
    *,
    allow_external: bool = False,
) -> None:
    """Build the container + app and run uvicorn. Used by `__main__`."""
    bind_host = host or "127.0.0.1"
    # 기동 단계 fail-closed 게이트: 비루프백 바인딩은 그 자체로
    # "네트워크의 모든 이웃에게 API 를 연다"는 뜻이다. Host/Origin 헤더 검사는
    # 클라이언트가 임의로 채울 수 있어 네트워크 경계가 아니므로, 여기서
    # (1) 명시적 위험 플래그와 (2) 비어 있지 않은 토큰을 **둘 다** 요구한다.
    if bind_host not in _LOOPBACK_HOSTS:
        if not allow_external:
            raise SystemExit(
                f"refusing to bind non-loopback host {bind_host!r}: "
                "pass --allow-external (and --token) to expose the API beyond localhost"
            )
        if not token:
            raise SystemExit(
                f"refusing to bind non-loopback host {bind_host!r} without a token: "
                "an empty --token disables auth entirely"
            )

    import uvicorn

    from ..core.config import RuntimeArgs

    # 실제 바인딩 포트를 먼저 확정한 뒤 컨테이너에 주입한다 — OAuth redirect_uri
    # (http://localhost:{port}/connectors/oauth/callback)가 바인딩 포트와 일치해야 한다.
    bind_port = port or 8080
    container = build_container(RuntimeArgs(host=bind_host, port=bind_port, token=token))
    app = create_app(container)
    log.info("rice-runtime listening on %s:%s", container.args.host, bind_port)
    # uvicorn.run() 대신 Server 를 직접 들고 있는다 — POST /shutdown 이
    # `should_exit` 를 세워 프로세스가 실제로 종료되게 하기 위함
    # 이전엔 endpoint 가 DB/provider 만 닫아 "요청은 받되 내부는 닫힌" 좀비가 됐다).
    server = uvicorn.Server(uvicorn.Config(app, host=container.args.host, port=bind_port, log_level="info"))
    app.state.uvicorn_server = server
    server.run()
