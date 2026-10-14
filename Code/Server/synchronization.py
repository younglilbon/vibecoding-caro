"""Versioned, permission-scoped publication of authoritative room state."""

import copy
import logging
import threading
from dataclasses import dataclass
from typing import Any

from Code.Server.client_handler import ClientHandler
from Code.Shared.message_types import MessageType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomAudience:
    """Authorized room recipients, with a reserved list for future viewers."""

    players: tuple[ClientHandler, ...] = ()
    viewers: tuple[ClientHandler, ...] = ()

    @property
    def connections(self) -> tuple[ClientHandler, ...]:
        """Return each explicitly authorized connection once."""
        recipients: list[ClientHandler] = []
        seen: set[int] = set()
        for connection in (*self.players, *self.viewers):
            identity = id(connection)
            if identity not in seen:
                seen.add(identity)
                recipients.append(connection)
        return tuple(recipients)

    def authorizes(self, connection: ClientHandler) -> bool:
        return any(recipient is connection for recipient in self.connections)


class Synchronization:
    """Publish server-owned state with monotonic per-room versions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._publish_locks: dict[str, threading.Lock] = {}
        self._versions: dict[str, int] = {}
        self._current_states: dict[str, dict[str, Any]] = {}

    def publish_state(
        self,
        room_id: str,
        state: dict[str, Any],
        audience: RoomAudience,
        *,
        event: str = "STATE",
        disconnected_player: str | None = None,
    ) -> int:
        """Version and broadcast an authoritative state to its room audience."""
        if not room_id:
            raise ValueError("room_id is required")

        with self._lock:
            publish_lock = self._publish_locks.setdefault(room_id, threading.Lock())
            publish_lock.acquire()
            version = self._versions.get(room_id, 0) + 1
            message = copy.deepcopy(state)
            message["type"] = MessageType.ROOM_UPDATE.value
            message["room_id"] = room_id
            message["version"] = version
            message["authority"] = "SERVER"
            message["event"] = event
            if disconnected_player is not None:
                message["disconnected_player"] = disconnected_player
            self._versions[room_id] = version
            self._current_states[room_id] = copy.deepcopy(message)
            recipients = audience.connections

        try:
            for recipient in recipients:
                self._send(recipient, message, room_id, version)
        finally:
            publish_lock.release()
        return version

    def notify_player_disconnected(
        self,
        room_id: str,
        state: dict[str, Any],
        audience: RoomAudience,
        player_name: str,
    ) -> int:
        """Publish the updated room state with a disconnect event marker."""
        return self.publish_state(
            room_id,
            state,
            audience,
            event="PLAYER_DISCONNECTED",
            disconnected_player=player_name,
        )

    def send_current_state(
        self,
        room_id: str,
        connection: ClientHandler,
        audience: RoomAudience,
    ) -> bool:
        """Send the cached latest version to an explicitly authorized connection."""
        if not audience.authorizes(connection):
            return False

        with self._lock:
            publish_lock = self._publish_locks.get(room_id)
            current_state = self._current_states.get(room_id)
            if publish_lock is None or current_state is None:
                return False
            publish_lock.acquire()
            message = copy.deepcopy(current_state)

        try:
            return self._send(connection, message, room_id, message["version"])
        finally:
            publish_lock.release()

    def get_version(self, room_id: str) -> int:
        """Return the latest committed state version, or zero for a new room."""
        with self._lock:
            return self._versions.get(room_id, 0)

    def get_current_state(self, room_id: str) -> dict[str, Any] | None:
        """Return a defensive copy of the latest server-owned room update."""
        with self._lock:
            state = self._current_states.get(room_id)
            return copy.deepcopy(state) if state is not None else None

    @staticmethod
    def _send(
        connection: ClientHandler,
        message: dict[str, Any],
        room_id: str,
        version: int,
    ) -> bool:
        try:
            connection.send_message(message)
            return True
        except OSError as exc:
            logger.info(
                "State delivery failed for room %s version %s (%s)",
                room_id,
                version,
                type(exc).__name__,
            )
            return False