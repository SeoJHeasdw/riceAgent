"""CLI entrypoint: `rice-runtime --port {p} --token {t}` (CONTRACTS §1.1).

The Electron supervisor spawns this with a dynamically chosen port and a one-shot
auth token. Binds localhost only.
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="rice-runtime")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--token", type=str, default="")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--log-level", type=str, default="info")
    # PyInstaller bundles cannot execute ``sys.executable -c`` because
    # sys.executable is the frozen app. The sandbox recursively invokes this
    # private mode instead; it is intentionally omitted from --help.
    parser.add_argument("--sandbox-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sandbox-path", action="append", default=[], help=argparse.SUPPRESS)
    # 비루프백 바인딩은 명시적 위험 플래그 + 강제 토큰 둘 다 있어야 한다
    # (RUNTIME_DESIGN §10.1). serve()가 기동 단계에서 fail-closed 로 검증한다.
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="DANGEROUS: allow binding a non-loopback host (requires --token).",
    )
    args = parser.parse_args()

    if args.sandbox_worker:
        from .execution.sandbox.ipc import run_frozen_child_worker

        run_frozen_child_worker(args.sandbox_path)
        return

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from .server.app import serve

    serve(host=args.host, port=args.port, token=args.token, allow_external=args.allow_external)


if __name__ == "__main__":
    main()
