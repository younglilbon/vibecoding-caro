"""Lifecycle and message loop for one TCP client connection."""

import logging
import socket
import threading
from enum import Enum
from typing import Any, Callable

from Code.Shared.message_types import MessageType
from Code.Shared.protocol import (
    ConnectionClosedError,
    InvalidMessageError,
    MessageProtocol,
)

logger = logging.getLogger(__name__)


class ClientState(str, Enum):
    """Lifecycle states for a client connection."""

    CONNECTED = "CONNECTED"
    RUNNING = "RUNNING"
    CLOSED = "CLOSED"


MessageCallback = Callable[["ClientHandler", dict[str, Any]], None]
DisconnectCallback = Callable[["ClientHandler"], None]


class ClientHandler:
    """Receive framed messages from one client until disconnect or timeout."""

    def __init__(
        self,
        connection: socket.socket,
        address: tuple[str, int],
        timeout: float = 60.0,
        on_message: MessageCallback | None = None,
        on_close: Callable[["ClientHandler"], None] | None = None,
    ) -> None:
        self.connection = connection
        self.address = address
        self.timeout = timeout
        self.on_message = on_message
        self.on_close = on_close
        self.state = ClientState.CONNECTED
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()

    def send_message(self, message: dict[str, Any]) -> None:
        """Serialize framed writes so concurrent broadcasts cannot interleave."""
        with self._send_lock:
            MessageProtocol.send_message(self.connection, message)

    def stop(self) -> None:
        """Interrupt a blocked read so the handler can finish promptly."""
        self._stop_event.set()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def run(self) -> None:
        """Process this client's messages and always close its socket."""
        self.state = ClientState.RUNNING
        buffer = bytearray()
        logger.info("Client connected: %s", self.address)
        try:
            self.connection.settimeout(self.timeout)
            while not self._stop_event.is_set():
                try:
                    message, buffer = MessageProtocol.receive_message(self.connection, buffer)
                except ConnectionClosedError:
                    logger.info("Client disconnected: %s", self.address)
                    break
                except InvalidMessageError:
                    logger.warning("Invalid protocol data from client: %s", self.address)
                    self._send_error()
                    break
                except OSError as exc:
                    if not self._stop_event.is_set():
                        logger.warning("Client I/O error from %s (%s)", self.address, type(exc).__name__)
                    break

                if message is None:
                    logger.info("Client timed out: %s", self.address)
                    break

                message_type = message.get("type")
                if not isinstance(message_type, str):
                    logger.warning("Unsupported message type from client: %s", self.address)
                    self._send_error()
                    break
                try:
                    MessageType(message_type)
                except ValueError:
                    logger.warning("Unsupported message type from client: %s", self.address)
                    self._send_error()
                    break

                if self.on_message is not None:
                    self.on_message(self, message)
                else:
                    logger.info("Received %s from %s", message_type, self.address)
        except Exception as exc:
            logger.error("Client handler failed for %s (%s)", self.address, type(exc).__name__)
        finally:
            self.state = ClientState.CLOSED
            self.connection.close()
            logger.info("Client connection closed: %s", self.address)
            if self.on_close is not None:
                self.on_close(self)

    def _send_error(self) -> None:
        try:
            self.send_message({"type": MessageType.ERROR.value, "message": "Invalid message"})
        except OSError:
            pass