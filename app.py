"""Command-line entry point for Anyworld."""

import argparse
import logging
import sys

import uvicorn

from core.config import settings


def main() -> None:
    try:
        settings.server.validate_passwords()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    parser = argparse.ArgumentParser(description="LLM RPG Orchestrator Gateway")
    parser.add_argument(
        "--host", type=str, default=settings.server.host, help="Bind socket to this host."
    )
    parser.add_argument(
        "--port", type=int, default=settings.server.port, help="Bind socket to this port."
    )
    parser.add_argument("--reload", action="store_true", help="Enable development auto-reload.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.info("Launching Anyworld on %s:%s", args.host, args.port)
    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        # Keep idle WAN WebSocket connections alive through NAT/proxies.
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
