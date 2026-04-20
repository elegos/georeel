"""Entry point for the standalone ``georeel-server`` command."""

import argparse
import logging
import sys
from dataclasses import dataclass

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass
class _Args:
    host: str
    port: int
    log_level: str


def _parse_args(argv: list[str] | None = None) -> _Args:
    parser = argparse.ArgumentParser(
        prog="georeel-server",
        description="GeoReel REST API server",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Bind port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        dest="log_level",
        help="Logging verbosity (default: info)",
    )
    ns = parser.parse_args(argv)
    return _Args(host=str(ns.host), port=int(ns.port), log_level=str(ns.log_level))


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    from georeel.server.app import app  # noqa: PLC0415  (import after logging setup)

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
