"""Command-line entry point for Anyworld."""

import argparse
import logging
import re
import sys

import uvicorn

from core.config import settings
from api.tls_bootstrap import ensure_cert

_RESPONSE_STATUS = re.compile(r'(\d{3})(?:\s+\w+)?\s*"?\s*$')


class _NonSuccessOnly(logging.Filter):
    """Silence uvicorn access and httpx lines that ended in a 2xx status."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Keep records that are not 2xx access lines; drop 2xx status lines."""
        match = _RESPONSE_STATUS.search(record.getMessage())
        return match is None or not match.group(1).startswith("2")


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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    quiet_filter = _NonSuccessOnly()
    for target_logger in (logging.getLogger("uvicorn.access"), logging.getLogger("httpx")):
        target_logger.setLevel(logging.INFO)
        target_logger.addFilter(quiet_filter)
    ip, cert_path, key_path = ensure_cert()
    logging.info("Launching Anyworld on %s:%s (https://%s:%s)", args.host, args.port, ip, args.port)
    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path,
        # Keep idle WAN WebSocket connections alive through NAT/proxies.
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
