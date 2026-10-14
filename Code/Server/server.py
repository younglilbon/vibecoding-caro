"""Threaded TCP server for framed Caro client messages."""

import logging
import socket
import threading
from collections.abc import Sequence
from typing import Any

from Code.Shared.constants import DEFAULT_HOST, DEFAULT_PORT
from Code.Server.client_handler import ClientHandler, DisconnectCallback, MessageCallback

logger = logging.getLogger(__name__)


class TCPServer:
    """Accept clients concurrently and isolate each connection in its thread."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        client_timeout: float = 60.0,
        backlog: int = 16,
        on_message: MessageCallback | None = None,
        on_disconnect: DisconnectCallback | None = None,
    ) -> None:
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if client_timeout <= 0:
            raise ValueError("client_timeout must be greater than zero")

        self.host = host
        self.port = port
        self.client_timeout = client_timeout
        self.backlog = backlog
        self.on_message = on_message or self._log_message
        self.on_disconnect = on_disconnect
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._handlers: dict[ClientHandler, threading.Thread] = {}
        self._address: tuple[str, int] | None = None

    @property
    def address(self) -> tuple[str, int] | None:
        """Return the bound host and port once the server has started."""
        return self._address

    @property
    def is_running(self) -> bool:
        """Whether the accept loop is currently active."""
        thread = self._accept_thread
        return thread is not None and thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> tuple[str, int]:
        """Bind the configured address and start the accept thread."""
        with self._lock:
            if self._accept_thread is not None and self._accept_thread.is_alive():
                raise RuntimeError("server is already running")

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((self.host, self.port))
                listener.listen(self.backlog)
                listener.settimeout(0.25)
            except OSError:
                listener.close()
                raise

            bound = listener.getsockname()
            self._address = (str(bound[0]), int(bound[1]))
            self._listener = listener
            self._stop_event.clear()
            self._accept_thread = threading.Thread(
                target=self._accept_loop,
                name="caro-server-accept",
                daemon=True,
            )
            self._accept_thread.start()
            return self._address

    def stop(self) -> None:
        """Stop accepting clients, close active connections, and join threads."""
        self._stop_event.set()
        with self._lock:
            listener, self._listener = self._listener, None
            active = list(self._handlers.items())

        if listener is not None:
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            listener.close()

        for handler, _ in active:
            handler.stop()

        accept_thread = self._accept_thread
        if accept_thread is not None and accept_thread is not threading.current_thread():
            accept_thread.join()
        for _, client_thread in active:
            if client_thread is not threading.current_thread():
                client_thread.join()
        logger.info("Server stopped")

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            listener = self._listener
            if listener is None:
                break
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop_event.is_set():
                    logger.error("Server accept failed (%s)", type(exc).__name__)
                break

            handler = ClientHandler(
                connection,
                (str(address[0]), int(address[1])),
                timeout=self.client_timeout,
                on_message=self.on_message,
                on_close=self._handle_client_closed,
            )
            client_thread = threading.Thread(
                target=handler.run,
                name=f"caro-client-{address[0]}:{address[1]}",
                daemon=True,
            )
            with self._lock:
                if self._stop_event.is_set():
                    connection.close()
                    break
                self._handlers[handler] = client_thread
            client_thread.start()

    def _handle_client_closed(self, handler: ClientHandler) -> None:
        with self._lock:
            self._handlers.pop(handler, None)
        if self.on_disconnect is not None:
            try:
                self.on_disconnect(handler)
            except Exception as exc:
                logger.error("Disconnect callback failed (%s)", type(exc).__name__)

    @staticmethod
    def _log_message(_handler: ClientHandler, message: dict[str, Any]) -> None:
        logger.info("Received message type %s", message.get("type", "unknown"))


def main(argv: Sequence[str] | None = None) -> None:
    """Compatibility entry point; use ``python -m Code.Server.main``."""
    from Code.Server.main import main as run_main

    run_main(argv)


if __name__ == "__main__":
    main()
