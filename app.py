"""Command-line entry point for Anyworld."""

import argparse
import logging
import os
import sys

import uvicorn

from core.config import settings
from core.console_logging import ConsoleHandler, LOGGING_CONFIG
from api.tls_bootstrap import ensure_cert

LOGGER = logging.getLogger("app")


def main() -> None:
    """Parse CLI arguments, configure logging and launch the HTTPS gateway."""
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
    parser.add_argument(
        "--debug-raw-responses",
        "--debug",
        action="store_true",
        help="Save private raw LLM responses under .debug/llm/.",
    )
    args = parser.parse_args()
    if args.debug_raw_responses:
        settings.llm.debug_raw_responses = True
        # Uvicorn reload starts a fresh interpreter; carry the launch override there too.
        os.environ["ANYWORLD_DEBUG_RAW_RESPONSES"] = "1"

    logging.basicConfig(
        level=logging.INFO,
        handlers=[ConsoleHandler()],
    )
    ip, cert_path, key_path = ensure_cert()
    LOGGER.info("Launching Anyworld on %s:%s (https://%s:%s)", args.host, args.port, ip, args.port)
    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=LOGGING_CONFIG,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path,
        # Keep idle WAN WebSocket connections alive through NAT/proxies.
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
