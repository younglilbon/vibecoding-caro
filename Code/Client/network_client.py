"""Background TCP client using the project's shared framed-message protocol."""

import copy
import logging
import queue
import socket
import threading
from dataclasses import dataclass
from typing import Any

from Code.Shared.protocol import ConnectionClosedError, InvalidMessageError, MessageProtocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NetworkEvent:
    """An event for the Tk main thread to consume from a queue."""

    kind: str
    payload: dict[str, Any]


class NetworkClient:
    """Own one socket worker; UI code only enqueues outgoing messages."""

    def __init__(
        self,
        events: queue.Queue[NetworkEvent],
        *,
        connect_timeout: float = 4.0,
        poll_timeout: float = 0.2,
    ) -> None:
        self.events = events
        self.connect_timeout = connect_timeout
        self.poll_timeout = poll_timeout
        self._outgoing: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()
        self._socket_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        with self._socket_lock:
            return self._connected

    def connect(self, host: str, port: int) -> None:
        """Start a connection worker and return without blocking the caller."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Network client is already running")
        self._stop_event.clear()
        self._clear_outgoing()
        self._post("status", state="CONNECTING")
        self._thread = threading.Thread(
            target=self._run,
            args=(host, port),
            name="caro-network-client",
            daemon=True,
        )
        self._thread.start()

    def send(self, message: dict[str, Any]) -> bool:
        """Queue a message for the worker; this method performs no socket I/O."""
        if not self.connected or self._stop_event.is_set():
            return False
        self._outgoing.put(copy.deepcopy(message))
        return True

    def close(self) -> None:
        """Request shutdown and interrupt a pending read without joining on the UI thread."""
        self._stop_event.set()
        with self._socket_lock:
            connection = self._socket
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _run(self, host: str, port: int) -> None:
        connection: socket.socket | None = None
        connected_once = False
        disconnected_reason = "Connection closed"
        try:
            connection = socket.create_connection(
                (host, port),
                timeout=self.connect_timeout,
            )
            connection.settimeout(self.poll_timeout)
            with self._socket_lock:
                if self._stop_event.is_set():
                    connection.close()
                    return
                self._socket = connection
                self._connected = True
            connected_once = True
            self._post("status", state="CONNECTED")

            buffer = bytearray()
            while not self._stop_event.is_set():
                self._send_pending(connection)
                message, buffer = MessageProtocol.receive_message(connection, buffer)
                if message is not None:
                    self._post("message", message=message)
        except ConnectionClosedError:
            disconnected_reason = "Server closed the connection"
        except (OSError, InvalidMessageError) as exc:
            disconnected_reason = str(exc) or type(exc).__name__
            if not self._stop_event.is_set():
                self._post("error", message=disconnected_reason)
        except Exception as exc:
            disconnected_reason = type(exc).__name__
            logger.exception("Network worker failed")
            if not self._stop_event.is_set():
                self._post("error", message=disconnected_reason)
        finally:
            with self._socket_lock:
                self._connected = False
                if self._socket is connection:
                    self._socket = None
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
            if connected_once or not self._stop_event.is_set():
                self._post("status", state="DISCONNECTED", reason=disconnected_reason)

    def _send_pending(self, connection: socket.socket) -> None:
        for _ in range(32):
            try:
                message = self._outgoing.get_nowait()
            except queue.Empty:
                return
            MessageProtocol.send_message(connection, message)

    def _clear_outgoing(self) -> None:
        while True:
            try:
                self._outgoing.get_nowait()
            except queue.Empty:
                return

    def _post(self, kind: str, **payload: Any) -> None:
        self.events.put(NetworkEvent(kind, payload))