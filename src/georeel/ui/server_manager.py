"""Server lifecycle management.

The GUI tries to connect to an already-running georeel-server on the default
port (8765).  If nothing is listening it spawns its own subprocess on a random
free port and waits up to 30 s for it to become healthy.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time

from georeel.ui.server_client import ServerClient

_log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class ServerStartError(RuntimeError):
    """Raised when the server cannot be started within the timeout."""


class ServerManager:
    """Owns the georeel-server subprocess (when spawned) and the HTTP client."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._process: subprocess.Popen[bytes] | None = None
        self._client: ServerClient | None = None
        self._log_thread: threading.Thread | None = None

    # ── Public API ─────────────────────────────────────────────────────

    @property
    def client(self) -> ServerClient:
        if self._client is None:
            raise RuntimeError("Server not started; call start() first")
        return self._client

    def start(self) -> ServerClient:
        """Connect to an existing server or spawn a new one.

        Returns a ready ``ServerClient``.  Safe to call multiple times;
        subsequent calls return the already-connected client.
        """
        if self._client is not None:
            return self._client

        # Try the well-known port first.
        url = f"http://{self._host}:{self._port}"
        probe = ServerClient(url)
        if probe.is_alive():
            _log.info("Reusing existing georeel-server at %s", url)
            self._client = probe
            return self._client
        probe.close()

        # Nothing listening — spawn our own process on a random free port.
        port = _free_port()
        url = f"http://{self._host}:{port}"
        _log.info("Starting georeel-server on %s", url)
        self._process = subprocess.Popen(
            ["georeel-server", "--host", self._host, "--port", str(port),
             "--log-level", "debug"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._log_thread = threading.Thread(
            target=_forward_stderr,
            args=(self._process,),
            daemon=True,
            name="georeel-server-log",
        )
        self._log_thread.start()

        client = ServerClient(url)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if client.is_alive():
                self._client = client
                return self._client
            if self._process.poll() is not None:
                client.close()
                raise ServerStartError(
                    f"georeel-server process exited unexpectedly (port {port})"
                )
            time.sleep(0.2)

        client.close()
        self._process.terminate()
        self._process = None
        raise ServerStartError(
            f"georeel-server did not become healthy within 30 s (port {port})"
        )

    def stop(self) -> None:
        """Close the client and terminate the subprocess if we spawned it."""
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._process is not None:
            _log.info("Stopping georeel-server subprocess")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    """Return an OS-assigned free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


_SERVER_LOG = logging.getLogger("georeel.server")

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _forward_stderr(proc: subprocess.Popen[bytes]) -> None:
    """Read server stderr line-by-line and re-emit via the GUI logger."""
    assert proc.stderr is not None
    for raw in proc.stderr:
        line = raw.decode(errors="replace").rstrip()
        if not line:
            continue
        # uvicorn/georeel lines look like: "HH:MM:SS  LEVEL     name: message"
        # Try to parse the level; fall back to INFO.
        parts = line.split(None, 2)
        level = logging.INFO
        if len(parts) >= 2:
            level = _LEVEL_MAP.get(parts[1].rstrip(":"), logging.INFO)
        _SERVER_LOG.log(level, "[server] %s", line)
