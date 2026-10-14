"""Room lifecycle management backed by authenticated users and GameEngine."""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from Code.Server.client_handler import ClientHandler
from Code.Server.game_engine import GameEngine, GameState, Piece, Position
from Code.Server.match_history_store import MatchHistoryStore
from Code.Server.synchronization import RoomAudience, Synchronization
from Code.Server.user_manager import PlayerStatus, UserManager
from Code.Shared.message_types import MessageType

logger = logging.getLogger(__name__)
ROOM_CAPACITY = 2
MAX_SPECTATORS = 10
MAX_CHAT_LENGTH = 500
TURN_TIMEOUT = 30.0


class RoomError(ValueError):
    """Raised when a room operation violates membership or lifecycle rules."""


class RoomStatus(str, Enum):
    """Lifecycle state for a room and its match."""

    WAITING = "WAITING"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


@dataclass(frozen=True)
class RoomSeat:
    """A player name and the piece assigned to that player in the room."""

    player_name: str
    piece: Piece


@dataclass(frozen=True)
class RoomSnapshot:
    """Read-only room state safe to inspect or serialize for a client."""

    room_id: str
    host: str
    players: tuple[RoomSeat, ...]
    status: RoomStatus
    game_state: GameState | None = None
    current_player: Piece | None = None
    board: tuple[tuple[str, ...], ...] | None = None
    winner: str | None = None
    loser: str | None = None
    winning_piece: str | None = None
    winning_line: tuple[tuple[int, int], ...] | None = None
    reason: str | None = None
    spectators: tuple[str, ...] = ()
    history: tuple[dict[str, Any], ...] = ()
    remaining_seconds: float | None = field(default=None, compare=False)
    replay_requests: tuple[str, ...] = ()


@dataclass
class _Room:
    room_id: str
    host_connection: ClientHandler
    host_name: str
    seats: list[tuple[ClientHandler, RoomSeat]]
    spectators: dict[ClientHandler, str] = field(default_factory=dict)
    status: RoomStatus = RoomStatus.WAITING
    engine: GameEngine | None = None
    disconnected_player: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    round_number: int = 1
    chat_history: list[dict[str, Any]] = field(default_factory=list)
    replay_requests: set[str] = field(default_factory=set)
    winner: str | None = None
    loser: str | None = None
    end_reason: str | None = None
    result_state: GameState | None = None
    turn_deadline: float | None = None
    remaining_seconds: float = TURN_TIMEOUT
    turn_timer: threading.Timer | None = None
    round_started_at: str | None = None
    publish_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class RoomManager:
    """Manage two-player rooms; win and draw rules remain in GameEngine."""

    def __init__(
        self,
        user_manager: UserManager,
        synchronization: Synchronization | None = None,
        *,
        turn_timeout: float = TURN_TIMEOUT,
        history_store: MatchHistoryStore | None = None,
    ) -> None:
        if turn_timeout <= 0:
            raise ValueError("turn_timeout must be greater than zero")
        self.user_manager = user_manager
        self.synchronization = synchronization or Synchronization()
        self.turn_timeout = turn_timeout
        self.history_store = history_store
        self._lock = threading.RLock()
        self._rooms: dict[str, _Room] = {}
        self._room_by_connection: dict[ClientHandler, str] = {}
        self._spectator_room_by_connection: dict[ClientHandler, str] = {}
        self.user_manager.set_reconnect_callbacks(self.reconnect_player, self.expire_player)

    def create_room(self, connection: ClientHandler) -> RoomSnapshot:
        """Create a waiting room for an authenticated, available player."""
        player = self._require_player(connection)
        with self._lock:
            existing_room_id = self._room_by_connection.get(connection)
            existing_room = self._rooms.get(existing_room_id) if existing_room_id else None
            if existing_room is not None and existing_room.status is RoomStatus.ENDED:
                self._room_by_connection.pop(connection, None)
                self.user_manager.transition_status(
                    connection, PlayerStatus.IN_ROOM, PlayerStatus.ONLINE
                )
            if connection in self._room_by_connection:
                raise RoomError("Player is already in a room")
            if not self.user_manager.transition_status(
                connection, PlayerStatus.ONLINE, PlayerStatus.IN_ROOM
            ):
                raise RoomError("Player is not available for room activity")
            room_id = uuid.uuid4().hex
            room = _Room(
                room_id=room_id,
                host_connection=connection,
                host_name=player.name,
                seats=[(connection, RoomSeat(player.name, Piece.X))],
            )
            self._rooms[room_id] = room
            self._room_by_connection[connection] = room_id
            snapshot = self._snapshot(room)
            room.publish_lock.acquire()
        logger.info("Room created: %s by %s", room_id, player.name)
        self._publish_room(room, snapshot, [connection])
        return snapshot

    def join_room(self, connection: ClientHandler, room_id: str) -> RoomSnapshot:
        """Join a waiting room as O; a room never accepts a third player."""
        player = self._require_player(connection)
        with self._lock:
            if connection in self._room_by_connection:
                raise RoomError("Player is already in a room")
            room = self._rooms.get(room_id)
            if room is None or room.status is RoomStatus.ENDED:
                raise RoomError("Room does not exist")
            if room.status is not RoomStatus.WAITING:
                raise RoomError("Room is full or no longer accepting players")
            if len(room.seats) >= ROOM_CAPACITY:
                raise RoomError("Room is full")
            if any(seat.player_name.casefold() == player.name.casefold() for _, seat in room.seats):
                raise RoomError("Player is already in this room")
            if not self.user_manager.transition_status(
                connection, PlayerStatus.ONLINE, PlayerStatus.IN_ROOM
            ):
                raise RoomError("Player is not available for room activity")

            room.seats.append((connection, RoomSeat(player.name, Piece.O)))
            room.status = RoomStatus.READY
            self._room_by_connection[connection] = room_id
            snapshot = self._snapshot(room)
            recipients = [member for member, _ in room.seats]
            room.publish_lock.acquire()
        logger.info("Player %s joined room %s", player.name, room_id)
        self._publish_room(room, snapshot, recipients)
        return snapshot

    def leave_room(
        self, connection: ClientHandler, room_id: str | None = None
    ) -> RoomSnapshot | None:
        """Leave a room; an in-progress match ends without assigning a winner."""
        self._require_player(connection)
        return self._remove_from_room(connection, room_id)

    def disconnect(
        self,
        connection: ClientHandler,
        *,
        player_name: str | None = None,
    ) -> RoomSnapshot | None:
        """Detach a disconnected player, pausing an active match for reconnect."""
        spectator_room_id = self._spectator_room_by_connection.pop(connection, None)
        if spectator_room_id is not None:
            with self._lock:
                room = self._rooms.get(spectator_room_id)
                if room is not None:
                    room.spectators.pop(connection, None)
                    self.user_manager.transition_status(
                        connection, PlayerStatus.IN_ROOM, PlayerStatus.ONLINE
                    )
                    snapshot = self._snapshot(room)
                    recipients = self._connected_members(room)
                    room.publish_lock.acquire()
                else:
                    snapshot = None
                    recipients = []
            if snapshot is not None:
                self._publish_room(room, snapshot, recipients)
            return snapshot

        player = self.user_manager.get_player(connection)
        if player is None and player_name is None:
            return None
        disconnected_name = player.name if player is not None else player_name
        assert disconnected_name is not None
        with self._lock:
            room_id = self._room_by_connection.get(connection)
            room = self._rooms.get(room_id) if room_id is not None else None
            if room is not None and room.status is RoomStatus.IN_PROGRESS:
                room.status = RoomStatus.PAUSED
                room.disconnected_player = disconnected_name
                if room.turn_deadline is not None:
                    room.remaining_seconds = max(0.0, room.turn_deadline - time.monotonic())
                self._cancel_turn_timer(room)
                self._room_by_connection.pop(connection, None)
                snapshot = self._snapshot(room)
                recipients = self._connected_members(room)
                room.publish_lock.acquire()
            else:
                snapshot = None
                recipients = []
        if room is not None and snapshot is not None:
            self._publish_room(
                room,
                snapshot,
                recipients,
                event="PLAYER_DISCONNECTED",
                disconnected_player=disconnected_name,
            )
            return snapshot
        return self._remove_from_room(connection, None, disconnected=True)

    def reconnect_player(self, connection: ClientHandler, player_name: str) -> None:
        """Restore a paused player seat and resume the server-side turn clock."""
        with self._lock:
            room = next(
                (
                    candidate
                    for candidate in self._rooms.values()
                    if candidate.status is RoomStatus.PAUSED
                    and candidate.disconnected_player is not None
                    and candidate.disconnected_player.casefold() == player_name.casefold()
                ),
                None,
            )
            if room is None:
                return
            room.seats = [
                (connection, seat) if seat.player_name.casefold() == player_name.casefold()
                else (member, seat)
                for member, seat in room.seats
            ]
            room.disconnected_player = None
            room.status = RoomStatus.IN_PROGRESS
            self._room_by_connection[connection] = room.room_id
            room.turn_deadline = time.monotonic() + room.remaining_seconds
            self._schedule_turn_timer(room)
            snapshot = self._snapshot(room)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients, event="PLAYER_RECONNECTED")

    def expire_player(self, player_name: str) -> None:
        """End a paused match when its disconnected player misses the resume window."""
        with self._lock:
            room = next(
                (
                    candidate
                    for candidate in self._rooms.values()
                    if candidate.status is RoomStatus.PAUSED
                    and candidate.disconnected_player is not None
                    and candidate.disconnected_player.casefold() == player_name.casefold()
                ),
                None,
            )
            if room is None:
                return
            opponent_seats = [
                seat for _, seat in room.seats
                if seat.player_name.casefold() != player_name.casefold()
            ]
            winner_seat = opponent_seats[0] if opponent_seats else None
            self._finish_match(
                room,
                winner_seat.player_name if winner_seat else None,
                player_name,
                "DISCONNECT_TIMEOUT",
                winner_seat.piece if winner_seat else None,
            )
            room.status = RoomStatus.ENDED
            self._cancel_turn_timer(room)
            snapshot = self._snapshot(room)
            self._persist_completed_match(room, snapshot)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients, event="GAME_END")

    def send_current_state(self, connection: ClientHandler, room_id: str) -> bool:
        """Resynchronize a current room member from the latest cached server state."""
        self._require_player(connection)
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or (
                self._room_by_connection.get(connection) != room_id
                and self._spectator_room_by_connection.get(connection) != room_id
            ):
                return False
            audience = RoomAudience(
                players=tuple(member for member, _ in room.seats),
                viewers=tuple(room.spectators),
            )
        return self.synchronization.send_current_state(room_id, connection, audience)

    def start_match(self, connection: ClientHandler, room_id: str) -> RoomSnapshot:
        """Start a ready match; only the host may start it."""
        self._require_player(connection)
        with self._lock:
            room = self._require_room(room_id)
            if self._room_by_connection.get(connection) != room_id:
                raise RoomError("Player is not a member of this room")
            self._require_room_player(connection)
            if room.host_connection is not connection:
                raise RoomError("Only the room host can start the match")
            if room.status is not RoomStatus.READY or len(room.seats) != ROOM_CAPACITY:
                raise RoomError("A match needs two players before it can start")

            room.engine = GameEngine()
            room.status = RoomStatus.IN_PROGRESS
            room.round_started_at = datetime.now(timezone.utc).isoformat()
            room.remaining_seconds = self.turn_timeout
            room.turn_deadline = time.monotonic() + self.turn_timeout
            self._schedule_turn_timer(room)
            snapshot = self._snapshot(room)
            recipients = [member for member, _ in room.seats]
            room.publish_lock.acquire()
        logger.info("Match started in room %s", room_id)
        self._publish_room(room, snapshot, recipients)
        return snapshot

    def make_move(
        self, connection: ClientHandler, room_id: str, row: int, col: int
    ) -> RoomSnapshot:
        """Validate membership and delegate move rules to the room GameEngine."""
        self._require_player(connection)
        with self._lock:
            room = self._require_room(room_id)
            if self._room_by_connection.get(connection) != room_id:
                raise RoomError("Player is not a member of this room")
            self._require_room_player(connection)
            if room.status is not RoomStatus.IN_PROGRESS or room.engine is None:
                raise RoomError("Match is not in progress")
            seat = next((seat for member, seat in room.seats if member is connection), None)
            if seat is None:
                raise RoomError("Player is not a member of this room")

            succeeded, reason = room.engine.make_move(Position(row, col), seat.piece)
            if not succeeded:
                raise RoomError(reason)
            room.history.append(
                {
                    "number": sum(
                        1 for item in room.history
                        if item.get("round") == room.round_number
                    ) + 1,
                    "round": room.round_number,
                    "player": seat.player_name,
                    "piece": seat.piece.value,
                    "row": row,
                    "col": col,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            if room.engine.get_game_state() is not GameState.IN_PROGRESS:
                room.status = RoomStatus.ENDED
                self._cancel_turn_timer(room)
                self._set_engine_result(room)
            else:
                room.remaining_seconds = self.turn_timeout
                room.turn_deadline = time.monotonic() + self.turn_timeout
                self._schedule_turn_timer(room)
            snapshot = self._snapshot(room)
            if snapshot.status is RoomStatus.ENDED:
                self._persist_completed_match(room, snapshot)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients)
        if snapshot.status is RoomStatus.ENDED:
            game_end_payload = {
                "type": MessageType.GAME_END.value,
                "room_id": room.room_id,
                "status": "ENDED",
                "game_state": snapshot.game_state.value if snapshot.game_state is not None else None,
                "winner": snapshot.winner,
                "loser": snapshot.loser,
                "winning_piece": snapshot.winning_piece,
                "winning_line": [list(coord) for coord in snapshot.winning_line] if snapshot.winning_line is not None else None,
                "reason": snapshot.reason,
            }
            for member in recipients:
                self._send(member, game_end_payload)
        return snapshot

    def get_rooms(self, *, include_ended: bool = False) -> list[RoomSnapshot]:
        """Return snapshots of active rooms, optionally including ended matches."""
        with self._lock:
            rooms = [
                self._snapshot(room)
                for room in self._rooms.values()
                if include_ended or room.status is not RoomStatus.ENDED
            ]
        return sorted(rooms, key=lambda room: room.room_id)

    def close(self) -> None:
        """Cancel all turn timers when the server shuts down."""
        with self._lock:
            rooms = list(self._rooms.values())
            for room in rooms:
                self._cancel_turn_timer(room)

    def get_room(self, room_id: str) -> RoomSnapshot | None:
        """Return a room snapshot, including an ended room, if it still exists."""
        with self._lock:
            room = self._rooms.get(room_id)
            return self._snapshot(room) if room is not None else None

    def watch_room(self, connection: ClientHandler, room_id: str) -> RoomSnapshot:
        """Add a logged-in non-player to a room's read-only audience."""
        player = self._require_player(connection)
        with self._lock:
            if connection in self._room_by_connection or connection in self._spectator_room_by_connection:
                raise RoomError("Player is already in a room")
            room = self._require_room(room_id)
            if len(room.spectators) >= MAX_SPECTATORS:
                raise RoomError("Room spectator limit reached")
            if not self.user_manager.transition_status(
                connection, PlayerStatus.ONLINE, PlayerStatus.IN_ROOM
            ):
                raise RoomError("Player is not available for room activity")
            room.spectators[connection] = player.name
            self._spectator_room_by_connection[connection] = room_id
            snapshot = self._snapshot(room)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients, event="SPECTATOR_JOINED")
        for chat in room.chat_history:
            self._send(connection, {"type": MessageType.CHAT.value, **chat})
        return snapshot

    def leave_spectator(self, connection: ClientHandler) -> RoomSnapshot | None:
        """Remove a spectator and return the updated room snapshot."""
        with self._lock:
            room_id = self._spectator_room_by_connection.pop(connection, None)
            if room_id is None:
                return None
            room = self._rooms.get(room_id)
            self.user_manager.transition_status(
                connection, PlayerStatus.IN_ROOM, PlayerStatus.ONLINE
            )
            if room is None:
                return None
            room.spectators.pop(connection, None)
            snapshot = self._snapshot(room)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients, event="SPECTATOR_LEFT")
        return snapshot

    def send_chat(self, connection: ClientHandler, room_id: str, content: object) -> None:
        text = content.strip() if isinstance(content, str) else ""
        if not text or len(text) > MAX_CHAT_LENGTH:
            raise RoomError("Chat message must contain 1-500 characters")
        with self._lock:
            room = self._require_room(room_id)
            player = self.user_manager.get_player(connection)
            if player is None:
                raise RoomError("Login is required")
            if (
                self._room_by_connection.get(connection) != room_id
                and self._spectator_room_by_connection.get(connection) != room_id
            ):
                raise RoomError("Only room members can chat")
            chat = {
                "room_id": room_id,
                "sender": player.name,
                "content": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            room.chat_history.append(chat)
            recipients = self._connected_members(room)
        message = {"type": MessageType.CHAT.value, **chat}
        for recipient in recipients:
            self._send(recipient, message)

    def surrender(self, connection: ClientHandler, room_id: str) -> RoomSnapshot:
        """End an active match with the requesting player as the loser."""
        with self._lock:
            room = self._require_room(room_id)
            seat = self._get_member_seat(room, connection, room_id)
            if room.status is not RoomStatus.IN_PROGRESS:
                raise RoomError("Match is not in progress")
            opponent = next((entry for member, entry in room.seats if member is not connection), None)
            self._finish_match(
                room,
                opponent.player_name if opponent else None,
                seat.player_name,
                "SURRENDER",
                opponent.piece if opponent else None,
            )
            room.status = RoomStatus.ENDED
            self._cancel_turn_timer(room)
            snapshot = self._snapshot(room)
            self._persist_completed_match(room, snapshot)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients, event="GAME_END")
        return snapshot

    def request_replay(self, connection: ClientHandler, room_id: str, accepted: bool) -> RoomSnapshot:
        """Start a fresh round only after both seated players accept replay."""
        with self._lock:
            room = self._require_room(room_id)
            seat = self._get_member_seat(room, connection, room_id)
            if room.status is not RoomStatus.ENDED:
                raise RoomError("Replay can only be requested after a match ends")
            if not accepted:
                room.replay_requests.clear()
            else:
                room.replay_requests.add(seat.player_name.casefold())
            player_keys = {entry.player_name.casefold() for _, entry in room.seats}
            if accepted and room.replay_requests >= player_keys and len(player_keys) == ROOM_CAPACITY:
                room.engine = GameEngine()
                room.status = RoomStatus.IN_PROGRESS
                room.result_state = None
                room.winner = None
                room.loser = None
                room.end_reason = None
                room.disconnected_player = None
                room.round_number += 1
                room.round_started_at = datetime.now(timezone.utc).isoformat()
                room.replay_requests.clear()
                room.remaining_seconds = self.turn_timeout
                room.turn_deadline = time.monotonic() + self.turn_timeout
                self._schedule_turn_timer(room)
                event = "REPLAY_STARTED"
            else:
                event = "REPLAY_DECLINED" if not accepted else "REPLAY_REQUESTED"
            snapshot = self._snapshot(room)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients, event=event)
        return snapshot

    def handle_message(self, connection: ClientHandler, message: dict[str, Any]) -> bool:
        """Handle room protocol messages; return False for unrelated message types."""
        message_type = message.get("type")
        try:
            if message_type == MessageType.CREATE_ROOM.value:
                self.create_room(connection)
            elif message_type == MessageType.JOIN_ROOM.value:
                self.join_room(connection, self._string_field(message, "room_id"))
            elif message_type == MessageType.LEAVE_ROOM.value:
                room_id = message.get("room_id")
                if room_id is not None and not isinstance(room_id, str):
                    raise RoomError("Invalid room_id")
                spectator_room = self._spectator_room_by_connection.get(connection)
                if spectator_room is not None:
                    if room_id is not None and room_id != spectator_room:
                        raise RoomError("Player is not a member of this room")
                    snapshot = self.leave_spectator(connection)
                else:
                    snapshot = self.leave_room(connection, room_id)
                if snapshot is None:
                    self._send(
                        connection,
                        {"type": MessageType.ROOM_UPDATE.value, "room_id": room_id, "status": "LEFT"},
                    )
                else:
                    self._send(connection, self._room_message(snapshot))
            elif message_type == MessageType.ROOM_LIST.value:
                self._require_player(connection)
                self._send(
                    connection,
                    {
                        "type": MessageType.ROOM_LIST.value,
                        "rooms": [
                            self._room_summary(room)
                            for room in self.get_rooms(include_ended=True)
                        ],
                    },
                )
            elif message_type == MessageType.GAME_START.value:
                self.start_match(connection, self._string_field(message, "room_id"))
            elif message_type == MessageType.MOVE.value:
                room_id = self._string_field(message, "room_id")
                row, col = message.get("row"), message.get("col")
                if type(row) is not int or type(col) is not int:
                    raise RoomError("row and col must be integers")
                self.make_move(connection, room_id, row, col)
            elif message_type == MessageType.WATCH_ROOM.value:
                self.watch_room(connection, self._string_field(message, "room_id"))
            elif message_type == MessageType.CHAT.value:
                self.send_chat(
                    connection,
                    self._string_field(message, "room_id"),
                    message.get("content"),
                )
            elif message_type == MessageType.SURRENDER.value:
                self.surrender(connection, self._string_field(message, "room_id"))
            elif message_type == MessageType.PLAY_AGAIN.value:
                accepted = message.get("accepted")
                if type(accepted) is not bool:
                    raise RoomError("accepted must be a boolean")
                self.request_replay(
                    connection,
                    self._string_field(message, "room_id"),
                    accepted,
                )
            else:
                return False
        except RoomError as exc:
            self._send(connection, {"type": MessageType.ERROR.value, "message": str(exc)})
        return True

    def _schedule_turn_timer(self, room: _Room) -> None:
        deadline = room.turn_deadline
        if deadline is None:
            raise RuntimeError("Cannot schedule a turn without a deadline")
        timer, room.turn_timer = room.turn_timer, None
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(
            max(0.0, deadline - time.monotonic()),
            self._on_turn_timeout,
            args=(room.room_id, deadline),
        )
        timer.daemon = True
        room.turn_timer = timer
        timer.start()

    @staticmethod
    def _cancel_turn_timer(room: _Room) -> None:
        timer, room.turn_timer = room.turn_timer, None
        if timer is not None:
            timer.cancel()
        room.turn_deadline = None

    def _on_turn_timeout(self, room_id: str, deadline: float) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if (
                room is None
                or room.status is not RoomStatus.IN_PROGRESS
                or room.turn_deadline != deadline
                or room.engine is None
            ):
                return
            remaining = deadline - time.monotonic()
            if remaining > 0:
                self._schedule_turn_timer(room)
                return
            losing_piece = room.engine.get_current_player()
            losing_seat = next(
                (seat for _, seat in room.seats if seat.piece is losing_piece),
                None,
            )
            winning_seat = next(
                (seat for _, seat in room.seats if seat.piece is losing_piece.opponent),
                None,
            )
            self._finish_match(
                room,
                winning_seat.player_name if winning_seat else None,
                losing_seat.player_name if losing_seat else None,
                "TIMEOUT",
                winning_seat.piece if winning_seat else None,
            )
            room.status = RoomStatus.ENDED
            self._cancel_turn_timer(room)
            snapshot = self._snapshot(room)
            self._persist_completed_match(room, snapshot)
            recipients = self._connected_members(room)
            room.publish_lock.acquire()
        self._publish_room(room, snapshot, recipients, event="GAME_END")

    def _finish_match(
        self,
        room: _Room,
        winner: str | None,
        loser: str | None,
        reason: str,
        winning_piece: Piece | None,
    ) -> None:
        room.winner = winner
        room.loser = loser
        room.end_reason = reason
        room.result_state = (
            GameState.X_WON if winning_piece is Piece.X
            else GameState.O_WON if winning_piece is Piece.O
            else None
        )
        room.replay_requests.clear()

    @staticmethod
    def _set_engine_result(room: _Room) -> None:
        if room.engine is None:
            return
        state = room.engine.get_game_state()
        room.result_state = state
        if state in (GameState.X_WON, GameState.O_WON):
            winning_piece = Piece.X if state is GameState.X_WON else Piece.O
            room.winner = next(
                (seat.player_name for _, seat in room.seats if seat.piece is winning_piece),
                None,
            )
            room.loser = next(
                (seat.player_name for _, seat in room.seats if seat.piece is winning_piece.opponent),
                None,
            )
            room.end_reason = "FIVE_IN_A_ROW"
        elif state is GameState.DRAW:
            room.winner = None
            room.loser = None
            room.end_reason = "DRAW"
        room.replay_requests.clear()

    def _connected_members(self, room: _Room) -> list[ClientHandler]:
        recipients: list[ClientHandler] = []
        for connection, seat in room.seats:
            if (
                seat.player_name.casefold()
                != (room.disconnected_player or "").casefold()
                and self.user_manager.get_player(connection) is not None
            ):
                recipients.append(connection)
        recipients.extend(
            connection
            for connection in room.spectators
            if self.user_manager.get_player(connection) is not None
        )
        return recipients

    def _get_member_seat(
        self, room: _Room, connection: ClientHandler, room_id: str
    ) -> RoomSeat:
        if self._room_by_connection.get(connection) != room_id:
            raise RoomError("Only seated players can perform this action")
        seat = next((seat for member, seat in room.seats if member is connection), None)
        if seat is None:
            raise RoomError("Player is not a member of this room")
        return seat

    def _remove_from_room(
        self,
        connection: ClientHandler,
        requested_room_id: str | None,
        *,
        disconnected: bool = False,
    ) -> RoomSnapshot | None:
        with self._lock:
            current_room_id = self._room_by_connection.get(connection)
            if current_room_id is None:
                room = self._rooms.get(requested_room_id) if requested_room_id else None
                if (
                    room is not None
                    and room.status is RoomStatus.ENDED
                    and any(member is connection for member, _ in room.seats)
                ):
                    return None
                if requested_room_id is not None:
                    raise RoomError("Player is not in a room")
                return None
            if requested_room_id is not None and requested_room_id != current_room_id:
                raise RoomError("Player is not a member of this room")

            room = self._rooms[current_room_id]
            room.replay_requests.clear()
            departing_seat = next(
                (seat for member, seat in room.seats if member is connection),
                None,
            )
            disconnected_player = (
                departing_seat.player_name if disconnected and departing_seat is not None else None
            )
            event = "STATE"
            if room.status in (RoomStatus.IN_PROGRESS, RoomStatus.PAUSED):
                room.status = RoomStatus.ENDED
                room.disconnected_player = disconnected_player
                self._cancel_turn_timer(room)
                if disconnected:
                    winner_seat = next(
                        (seat for member, seat in room.seats if member is not connection),
                        None,
                    )
                    self._finish_match(
                        room,
                        winner_seat.player_name if winner_seat else None,
                        departing_seat.player_name if departing_seat else None,
                        "DISCONNECT_TIMEOUT",
                        winner_seat.piece if winner_seat else None,
                    )
                else:
                    winner_seat = next(
                        (seat for member, seat in room.seats if member is not connection),
                        None,
                    )
                    self._finish_match(
                        room,
                        winner_seat.player_name if winner_seat else None,
                        departing_seat.player_name if departing_seat else None,
                        "PLAYER_LEFT",
                        winner_seat.piece if winner_seat else None,
                    )
                    event = "PLAYER_LEFT"
                for member, _ in room.seats:
                    self._room_by_connection.pop(member, None)
                    if member is not connection or not disconnected:
                        self.user_manager.transition_status(
                            member, PlayerStatus.IN_ROOM, PlayerStatus.ONLINE
                        )
                snapshot = self._snapshot(room)
                self._persist_completed_match(room, snapshot)
                recipients = self._connected_members(room)
            else:
                was_ended = room.status is RoomStatus.ENDED
                room.seats = [(member, seat) for member, seat in room.seats if member is not connection]
                self._room_by_connection.pop(connection, None)
                self.user_manager.transition_status(
                    connection, PlayerStatus.IN_ROOM, PlayerStatus.ONLINE
                )
                if not room.seats:
                    if room.spectators:
                        room.status = RoomStatus.ENDED
                        snapshot = self._snapshot(room)
                        recipients = self._connected_members(room)
                    else:
                        del self._rooms[current_room_id]
                        snapshot = None
                        recipients = []
                else:
                    if room.host_connection is connection:
                        new_host, new_seat = room.seats[0]
                        room.host_connection = new_host
                        room.host_name = new_seat.player_name
                        if not was_ended:
                            room.seats[0] = (new_host, RoomSeat(new_seat.player_name, Piece.X))
                    room.status = RoomStatus.ENDED if was_ended else RoomStatus.WAITING
                    snapshot = self._snapshot(room)
                    recipients = self._connected_members(room)
            if snapshot is not None:
                room.publish_lock.acquire()

        logger.info("Player left room %s", current_room_id)
        if snapshot is not None:
            self._publish_room(
                room,
                snapshot,
                recipients,
                event=event,
                disconnected_player=disconnected_player,
            )
        return snapshot

    def _require_room_player(self, connection: ClientHandler) -> None:
        player = self._require_player(connection)
        if player.status is not PlayerStatus.IN_ROOM:
            raise RoomError("Player is not in an active room")

    def _require_player(self, connection: ClientHandler):
        player = self.user_manager.get_player(connection)
        if player is None:
            raise RoomError("Login is required")
        return player

    def _persist_completed_match(self, room: _Room, snapshot: RoomSnapshot) -> None:
        if self.history_store is None:
            return
        if room.round_started_at is None:
            raise RuntimeError("Cannot save a match without a start time")
        round_moves = [
            dict(move) for move in room.history
            if move.get("round") == room.round_number
        ]
        self.history_store.save_completed_match(
            room_id=room.room_id,
            round_number=room.round_number,
            host=room.host_name,
            players=[
                {"name": seat.player_name, "piece": seat.piece.value}
                for _, seat in room.seats
            ],
            winner=snapshot.winner,
            loser=snapshot.loser,
            game_state=snapshot.game_state.value if snapshot.game_state is not None else None,
            reason=room.end_reason,
            winning_piece=snapshot.winning_piece,
            winning_line=(
                [list(coordinate) for coordinate in snapshot.winning_line]
                if snapshot.winning_line is not None
                else None
            ),
            started_at=room.round_started_at,
            ended_at=datetime.now(timezone.utc).isoformat(),
            moves=round_moves,
        )

    def _require_room(self, room_id: str) -> _Room:
        room = self._rooms.get(room_id)
        if room is None:
            raise RoomError("Room does not exist")
        return room

    @staticmethod
    def _string_field(message: dict[str, Any], field: str) -> str:
        value = message.get(field)
        if not isinstance(value, str) or not value:
            raise RoomError(f"{field} is required")
        return value

    def _snapshot(self, room: _Room) -> RoomSnapshot:
        engine = room.engine
        winning_piece: str | None = None
        winning_line: tuple[tuple[int, int], ...] | None = None
        reason: str | None = None
        state = room.result_state
        if state is None and engine is not None:
            state = engine.get_game_state()
        if state in (GameState.X_WON, GameState.O_WON):
            winning_piece = "X" if state is GameState.X_WON else "O"
            if engine is not None and engine.get_game_state() is state:
                winning_line = tuple(engine.get_winning_line())
            if room.winner is None:
                room.winner = next(
                    (seat.player_name for _, seat in room.seats if seat.piece.value == winning_piece),
                    None,
                )
            if room.loser is None:
                room.loser = next(
                    (seat.player_name for _, seat in room.seats if seat.piece.value != winning_piece),
                    None,
                )
            reason = (
                f"{room.winner} wins with 5 in a row!"
                if room.end_reason == "FIVE_IN_A_ROW"
                else f"{room.winner or 'Opponent'} wins ({room.end_reason or 'MATCH_ENDED'})."
            )
        elif state is GameState.DRAW:
            reason = "Game ended in a draw! Board is full."
        elif room.end_reason:
            reason = f"Match ended: {room.end_reason}."
        remaining_seconds = room.remaining_seconds
        if room.turn_deadline is not None and room.status is RoomStatus.IN_PROGRESS:
            remaining_seconds = max(0.0, room.turn_deadline - time.monotonic())

        return RoomSnapshot(
            room_id=room.room_id,
            host=room.host_name,
            players=tuple(seat for _, seat in room.seats),
            status=room.status,
            game_state=state,
            current_player=engine.get_current_player() if engine is not None else None,
            board=(
                tuple(tuple(piece.value for piece in row) for row in engine.get_board())
                if engine is not None
                else None
            ),
            winner=room.winner,
            loser=room.loser,
            winning_piece=winning_piece,
            winning_line=winning_line,
            reason=reason,
            spectators=tuple(sorted(room.spectators.values(), key=str.casefold)),
            history=tuple(dict(move) for move in room.history),
            remaining_seconds=remaining_seconds if engine is not None else None,
            replay_requests=tuple(sorted(room.replay_requests)),
        )

    @classmethod
    def _room_summary(cls, room: RoomSnapshot) -> dict[str, Any]:
        return {
            "room_id": room.room_id,
            "host": room.host,
            "players": [
                {"name": seat.player_name, "piece": seat.piece.value}
                for seat in room.players
            ],
            "capacity": ROOM_CAPACITY,
            "status": room.status.value,
            "spectator_count": len(room.spectators),
            "spectators": list(room.spectators),
        }

    @classmethod
    def _room_message(cls, room: RoomSnapshot, *, viewer: bool = False) -> dict[str, Any]:
        message = {"type": MessageType.ROOM_UPDATE.value, **cls._room_summary(room)}
        message.update(
            {
                "game_state": room.game_state.value if room.game_state is not None else None,
                "current_player": room.current_player.value if room.current_player is not None else None,
                "board": [list(row) for row in room.board] if room.board is not None else None,
                "winner": room.winner,
                "loser": room.loser,
                "winning_piece": room.winning_piece,
                "winning_line": [list(coord) for coord in room.winning_line] if room.winning_line is not None else None,
                "reason": room.reason,
                "spectators": list(room.spectators),
                "history": [dict(move) for move in room.history],
                "remaining_seconds": room.remaining_seconds,
                "replay_requests": list(room.replay_requests),
                "viewer": viewer,
            }
        )
        return message

    def _publish_room(
        self,
        room: _Room,
        snapshot: RoomSnapshot,
        recipients: list[ClientHandler],
        *,
        event: str = "STATE",
        disconnected_player: str | None = None,
    ) -> None:
        try:
            if event == "PLAYER_DISCONNECTED" and disconnected_player is not None:
                self._notify_room(
                    snapshot,
                    recipients,
                    event=event,
                    disconnected_player=disconnected_player,
                )
            elif event == "STATE":
                self._notify_room(snapshot, recipients)
            else:
                self._notify_room(snapshot, recipients, event=event)
        finally:
            room.publish_lock.release()
        self.user_manager.broadcast(
            {
                "type": MessageType.ROOM_LIST_UPDATE.value,
                "rooms": [
                    self._room_summary(candidate)
                    for candidate in self.get_rooms(include_ended=True)
                ],
            }
        )

    def _notify_room(
        self,
        room: RoomSnapshot,
        recipients: list[ClientHandler],
        *,
        event: str = "STATE",
        disconnected_player: str | None = None,
    ) -> None:
        audience = RoomAudience(players=tuple(recipients))
        payload = self._room_message(room)
        if event == "PLAYER_DISCONNECTED" and disconnected_player is not None:
            self.synchronization.notify_player_disconnected(
                room.room_id,
                payload,
                audience,
                disconnected_player,
            )
        else:
            self.synchronization.publish_state(
                room.room_id,
                payload,
                audience,
                event=event,
            )

    @staticmethod
    def _send(connection: ClientHandler, message: dict[str, Any]) -> bool:
        try:
            connection.send_message(message)
            return True
        except OSError:
            return False