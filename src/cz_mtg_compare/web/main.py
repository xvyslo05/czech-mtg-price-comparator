"""``cz-mtg-compare-web`` console-script entry point.

Usage::

    cz-mtg-compare-web                    # serves on 127.0.0.1:8080
    cz-mtg-compare-web --host 0.0.0.0     # listen on all interfaces
    cz-mtg-compare-web --port 9000        # custom port
    cz-mtg-compare-web --reload           # autoreload on file changes (dev)

The companion ``cz-mtg-compare-mcp`` entry point continues to ship the
stdio MCP server unchanged.
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="cz-mtg-compare-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
    )
    args = parser.parse_args()

    import uvicorn

    logging.basicConfig(level=args.log_level.upper())
    uvicorn.run(
        "cz_mtg_compare.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
