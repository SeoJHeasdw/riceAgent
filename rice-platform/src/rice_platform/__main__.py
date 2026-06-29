"""rice-platform entry point.

Usage:
    rice-platform [--host HOST] [--port PORT] [--artifacts-dir DIR]

Defaults: host=0.0.0.0, port=7070, artifacts-dir=./artifacts
"""

from __future__ import annotations

import argparse
import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="rice-platform market server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7070)
    parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="Directory containing artifact packages (default: <repo>/artifacts)",
    )
    args = parser.parse_args()

    import os
    if args.artifacts_dir:
        os.environ["RICE_ARTIFACTS_DIR"] = args.artifacts_dir

    uvicorn.run(
        "rice_platform.app:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
