"""Authenticated online-player and match-invitation lobby management."""

import logging
import secrets
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from Code.Server.client_handler import ClientHandler
from Code.Shared.message_types import MessageType

logger = logging.getLogger(__name__)


class PlayerStatus(str, Enum):
    """Lobby availability state; game sessions are intentionally out of scope."""

    ONLINE = "ONLINE"
    INVITE_PENDING = "INVITE_PENDING"
    IN_ROOM = "IN_ROOM"
    OFFLINE = "OFFLINE"


@dataclass
class Player:
    """A validated player identity bound to one client connection."""

    name: str
    connection: ClientHandler | None
    status: PlayerStatus = PlayerStatus.ONLINE
    resume_token: str = ""
    reconnect_timer: threading.Timer | None = None


@dataclass(frozen=True)
class Invitation:
    """A pending invitation whose ID is the only accepted response reference."""

    invite_id: str
    sender_key: str
    recipient_key: str
    sender_name: str
    recipient_name: str


class UserManager:
    """Manage lobby identities, online lists, and one outstanding invite per player."""

    MIN_NAME_LENGTH = 3
    MAX_NAME_LENGTH = 20

    def __init__(
        self,
        *,
        reconnect_timeout: float = 60.0,
        on_reconnect: Any = None,
        on_reconnect_expired: Any = None,
    ) -> None:
        if reconnect_timeout <= 0:
            raise ValueError("reconnect_timeout must be greater than zero")
        self._lock = threading.RLock()
        self._broadcast_lock = threading.Lock()
        self._players_by_key: dict[str, Player] = {}
        self._players_by_connection: dict[ClientHandler, Player] = {}
        self._invitations: dict[str, Invitation] = {}
        self.reconnect_timeout = reconnect_timeout
        self.on_reconnect = on_reconnect
        self.on_reconnect_expired = on_reconnect_expired

    @classmethod
    def is_valid_name(cls, name: object) -> bool:
        """Accept 3-20 letters/digits with optional internal '_' or '-' characters."""
        if not isinstance(name, str):
            return False
        cleaned = name.strip()
        return (
            cls.MIN_NAME_LENGTH <= len(cleaned) <= cls.MAX_NAME_LENGTH
            and cleaned[0].isalnum()
            and cleaned[-1].isalnum()
            and all(character.isalnum() or character in "_-" for character in cleaned)
        )

    def get_online_players(self) -> list[str]:
        """Return a stable, display-cased snapshot of online player names."""
        with self._lock:
            return sorted(
                (
                    player.name
                    for player in self._players_by_key.values()
                    if player.connection is not None
                ),
                key=str.casefold,
            )

    def set_reconnect_callbacks(self, on_reconnect: Any, on_expired: Any) -> None:
        self.on_reconnect = on_reconnect
        self.on_reconnect_expired = on_expired

    def close(self) -> None:
        """Cancel reconnect-expiry timers during server shutdown."""
        with self._lock:
            timers = [
                player.reconnect_timer
                for player in self._players_by_key.values()
                if player.reconnect_timer is not None
            ]
            for player in self._players_by_key.values():
                player.reconnect_timer = None
        for timer in timers:
            timer.cancel()

    def broadcast(self, message: dict[str, Any]) -> None:
        """Send a server notification to every currently connected player."""
        with self._lock:
            connections = [
                player.connection
                for player in self._players_by_key.values()
                if player.connection is not None and player.status is PlayerStatus.ONLINE
            ]
        for connection in connections:
            if connection is not None:
                self._send(connection, message)

    def get_player(self, connection: ClientHandler) -> Player | None:
        """Resolve the identity bound to a connection, if it has logged in."""
        with self._lock:
            return self._players_by_connection.get(connection)

    def transition_status(
        self,
        connection: ClientHandler,
        expected: PlayerStatus,
        new_status: PlayerStatus,
    ) -> bool:
        """Atomically change a connected player's status if it matches expected."""
        with self._lock:
            player = self._players_by_connection.get(connection)
            if player is None or player.status is not expected:
                return False
            player.status = new_status
            return True

    def handle_message(self, connection: ClientHandler, message: dict[str, Any]) -> bool:
        """Handle lobby message types; return False for unrelated game messages."""
        message_type = message.get("type")
        if message_type == MessageType.JOIN.value:
            self._join(connection, message.get("name"), message.get("resume_token"))
        elif message_type == MessageType.LEAVE.value:
            if self.disconnect(connection):
                connection.stop()
            else:
                self._send_error(connection, "Login is required")
        elif message_type == MessageType.INVITE.value:
            self._create_invitation(connection, message.get("target"))
        elif message_type == MessageType.INVITE_ACCEPT.value:
            self._respond_to_invitation(connection, message.get("invite_id"), accepted=True)
        elif message_type == MessageType.INVITE_REJECT.value:
            self._respond_to_invitation(connection, message.get("invite_id"), accepted=False)
        else:
            return False
        return True

    def disconnect(self, connection: ClientHandler) -> bool:
        """Temporarily detach a logged-in player while their resume token is valid."""
        cancelled: list[tuple[ClientHandler, dict[str, Any]]] = []
        with self._lock:
            player = self._players_by_connection.pop(connection, None)
            if player is None:
                return False
            player.connection = None
            if player.status is not PlayerStatus.IN_ROOM:
                player.status = PlayerStatus.OFFLINE
            timer = threading.Timer(
                self.reconnect_timeout,
                self._expire_player,
                args=(player.name.casefold(), player.resume_token),
            )
            timer.daemon = True
            player.reconnect_timer = timer
            timer.start()

            for invite_id, invitation in list(self._invitations.items()):
                if player.name.casefold() not in (invitation.sender_key, invitation.recipient_key):
                    continue
                del self._invitations[invite_id]
                other_key = (
                    invitation.recipient_key
                    if invitation.sender_key == player.name.casefold()
                    else invitation.sender_key
                )
                other = self._players_by_key.get(other_key)
                if other is not None:
                    other.status = PlayerStatus.ONLINE
                    if other.connection is not None:
                        cancelled.append(
                        (
                            other.connection,
                            self._invitation_result(invitation, "CANCELLED"),
                        )
                        )

        logger.info("Player disconnected: %s", player.name)
        for recipient, result in cancelled:
            self._send(recipient, result)
        self._broadcast_online_players()
        return True

    def logout(self, connection: ClientHandler) -> bool:
        """Permanently remove a player identity and invalidate its resume token."""
        with self._lock:
            player = self._players_by_connection.pop(connection, None)
            if player is None:
                return False
            self._players_by_key.pop(player.name.casefold(), None)
            player.connection = None
            timer, player.reconnect_timer = player.reconnect_timer, None
            for invite_id, invitation in list(self._invitations.items()):
                if player.name.casefold() in (
                    invitation.sender_key,
                    invitation.recipient_key,
                ):
                    del self._invitations[invite_id]
        if timer is not None:
            timer.cancel()
        self._broadcast_online_players()
        return True

    def _join(
        self,
        connection: ClientHandler,
        requested_name: object,
        requested_token: object = None,
    ) -> None:
        if not self.is_valid_name(requested_name):
            self._send_error(
                connection,
                "Name must be 3-20 characters and start/end with a letter or digit",
            )
            return

        name = requested_name.strip()
        name_key = name.casefold()
        resumed_player: Player | None = None
        with self._lock:
            if connection in self._players_by_connection:
                error = "This connection already has an identity"
            elif requested_token is not None:
                player = self._players_by_key.get(name_key)
                if (
                    not isinstance(requested_token, str)
                    or player is None
                    or player.connection is not None
                    or not secrets.compare_digest(player.resume_token, requested_token)
                ):
                    error = "Resume token is invalid or expired"
                else:
                    if player.reconnect_timer is not None:
                        player.reconnect_timer.cancel()
                        player.reconnect_timer = None
                    player.connection = connection
                    if player.status is PlayerStatus.OFFLINE:
                        player.status = PlayerStatus.ONLINE
                    self._players_by_connection[connection] = player
                    resumed_player = player
                    error = None
            elif name_key in self._players_by_key:
                error = "That player name is already online"
            else:
                player = Player(
                    name,
                    connection,
                    resume_token=secrets.token_urlsafe(32),
                )
                self._players_by_key[name_key] = player
                self._players_by_connection[connection] = self._players_by_key[name_key]
                error = None

        if error is not None:
            self._send_error(connection, error)
            return
        if resumed_player is not None and self.on_reconnect is not None:
            self.on_reconnect(connection, resumed_player.name)
        logger.info("Player connected: %s", name)
        self._broadcast_online_players()

    def _expire_player(self, player_key: str, token: str) -> None:
        with self._lock:
            player = self._players_by_key.get(player_key)
            if player is None or player.connection is not None or player.resume_token != token:
                return
            self._players_by_key.pop(player_key, None)
            player.reconnect_timer = None
            callback = self.on_reconnect_expired
        if callback is not None:
            callback(player.name)
        self._broadcast_online_players()

    def _create_invitation(self, connection: ClientHandler, requested_target: object) -> None:
        with self._lock:
            sender = self._players_by_connection.get(connection)
            if sender is None:
                error = "Login is required"
                recipient = None
                invitation = None
            elif not self.is_valid_name(requested_target):
                error = "Invalid target name"
                recipient = None
                invitation = None
            else:
                target_key = requested_target.strip().casefold()
                recipient = self._players_by_key.get(target_key)
                if recipient is None:
                    error = "Target player is not online"
                    invitation = None
                elif recipient is sender:
                    error = "You cannot invite yourself"
                    invitation = None
                elif (
                    sender.status is not PlayerStatus.ONLINE
                    or recipient.status is not PlayerStatus.ONLINE
                    or sender.connection is None
                    or recipient.connection is None
                ):
                    error = "One of the players is not available"
                    invitation = None
                else:
                    invite_id = uuid.uuid4().hex
                    invitation = Invitation(
                        invite_id,
                        sender.name.casefold(),
                        recipient.name.casefold(),
                        sender.name,
                        recipient.name,
                    )
                    self._invitations[invite_id] = invitation
                    sender.status = PlayerStatus.INVITE_PENDING
                    recipient.status = PlayerStatus.INVITE_PENDING
                    error = None

        if error is not None:
            self._send_error(connection, error)
            return

        assert sender is not None and recipient is not None and invitation is not None
        incoming = {
            "type": MessageType.INVITE.value,
            "invite_id": invitation.invite_id,
            "from": sender.name,
        }
        if not self._send(recipient.connection, incoming):
            self.disconnect(recipient.connection)
            return
        if not self._send(sender.connection, self._invitation_result(invitation, "PENDING")):
            self.disconnect(sender.connection)

    def _respond_to_invitation(
        self,
        connection: ClientHandler,
        invite_id: object,
        *,
        accepted: bool,
    ) -> None:
        with self._lock:
            player = self._players_by_connection.get(connection)
            invitation = self._invitations.get(invite_id) if isinstance(invite_id, str) else None
            if player is None:
                error = "Login is required"
                participants = None
            elif invitation is None:
                error = "Invitation is not pending"
                participants = None
            elif player.name.casefold() != invitation.recipient_key:
                error = "Only the invited player can respond"
                participants = None
            else:
                sender = self._players_by_key.get(invitation.sender_key)
                recipient = self._players_by_key.get(invitation.recipient_key)
                if (
                    sender is None
                    or recipient is None
                    or sender.status is not PlayerStatus.INVITE_PENDING
                    or recipient.status is not PlayerStatus.INVITE_PENDING
                ):
                    error = "Invitation participants are no longer available"
                    participants = None
                    self._invitations.pop(invitation.invite_id, None)
                else:
                    del self._invitations[invitation.invite_id]
                    sender.status = PlayerStatus.ONLINE
                    recipient.status = PlayerStatus.ONLINE
                    error = None
                    participants = (sender.connection, recipient.connection)

        if error is not None:
            self._send_error(connection, error)
            return

        assert invitation is not None and participants is not None
        status = "ACCEPTED" if accepted else "REJECTED"
        result = self._invitation_result(invitation, status)
        failed: list[ClientHandler] = []
        for participant in participants:
            if not self._send(participant, result):
                failed.append(participant)
        for failed_connection in failed:
            self.disconnect(failed_connection)

    def _broadcast_online_players(self) -> None:
        with self._broadcast_lock:
            with self._lock:
                players = sorted(
                    (player for player in self._players_by_key.values() if player.connection is not None),
                    key=lambda player: player.name.casefold(),
                )
                message = {
                    "type": MessageType.PLAYER_LIST.value,
                    "players": [player.name for player in players],
                }
                deliveries = [
                    (
                        player.connection,
                        {**message, "resume_token": player.resume_token},
                    )
                    for player in players
                ]
            for connection, player_message in deliveries:
                if connection is not None and not self._send(connection, player_message):
                    logger.info("Could not update lobby list for a disconnected client")

    @staticmethod
    def _invitation_result(invitation: Invitation, status: str) -> dict[str, str]:
        return {
            "type": MessageType.INVITE_RESULT.value,
            "invite_id": invitation.invite_id,
            "from": invitation.sender_name,
            "to": invitation.recipient_name,
            "status": status,
        }

    @staticmethod
    def _send(connection: ClientHandler, message: dict[str, Any]) -> bool:
        try:
            connection.send_message(message)
            return True
        except OSError:
            return False

    @classmethod
    def _send_error(cls, connection: ClientHandler, reason: str) -> None:
        cls._send(connection, {"type": MessageType.ERROR.value, "message": reason})