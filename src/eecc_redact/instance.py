"""One tray per user, and a way to reach it.

The tray listens on a per-user local socket. A second `eecc-redact` finds it taken and
asks the running one to show its settings, so clicking the launcher always does
something visible; `eecc-redact capture` asks it to capture, so the tray, which is
long-lived, owns the clipboard afterwards.
"""

import getpass
import time
from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from eecc_redact import APP_NAME


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return f"{APP_NAME}-tray-" + ("".join(ch for ch in user if ch.isalnum()) or "user")


def _connect(name: str) -> QLocalSocket | None:
    socket = QLocalSocket()
    socket.connectToServer(name)
    return socket if socket.waitForConnected(300) else None


def claim(on_message: Callable[[str], None], name: str | None = None) -> QLocalServer | None:
    """Become the one tray. Returns the server to keep, or None if one already runs."""
    name = name or server_name()
    if (socket := _connect(name)) is not None:
        socket.abort()
        return None
    QLocalServer.removeServer(name)  # a stale socket left behind by a crash
    server = QLocalServer()
    if not server.listen(name):
        return None

    def accept() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()

            def read(connection=connection) -> None:
                for line in bytes(connection.readAll()).decode(errors="replace").splitlines():
                    if line.strip():
                        on_message(line.strip())

            connection.readyRead.connect(read)
            if connection.bytesAvailable():
                read()

    server.newConnection.connect(accept)
    return server


def send(message: str, name: str | None = None) -> bool:
    """Deliver one message to the running tray. False if none is running."""
    socket = _connect(name or server_name())
    if socket is None:
        return False
    socket.write((message + "\n").encode())
    socket.flush()
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    return True


def request_quit(timeout: float = 5.0) -> bool:
    """Ask the running tray to exit and wait until it has. False if none was running."""
    name = server_name()
    if not send("quit", name):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (socket := _connect(name)) is None:
            return True
        socket.abort()
        time.sleep(0.1)
    return False
