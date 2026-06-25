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
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from .server.app import serve

    serve(host=args.host, port=args.port, token=args.token)


if __name__ == "__main__":
    main()
