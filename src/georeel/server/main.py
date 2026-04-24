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
    temp_dir: str | None
    reload: bool


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
    parser.add_argument(
        "--temp-dir",
        default=None,
        dest="temp_dir",
        help="Base directory for server temp files (default: system /tmp)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Auto-reload on code changes (development only)",
    )
    ns = parser.parse_args(argv)
    return _Args(
        host=str(ns.host),
        port=int(ns.port),
        log_level=str(ns.log_level),
        temp_dir=str(ns.temp_dir) if ns.temp_dir else None,
        reload=bool(ns.reload),
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    from pathlib import Path

    from georeel.core import temp_manager  # noqa: PLC0415
    if args.temp_dir:
        temp_manager.set_base_dir(Path(args.temp_dir))

    from georeel.server.app import app  # noqa: PLC0415  (import after logging setup)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
