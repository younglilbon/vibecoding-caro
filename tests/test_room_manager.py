"""Unit tests for RoomManager room and match lifecycle rules."""

import pytest
import threading

from Code.Server.game_engine import GameState, Piece
from Code.Server.room_manager import RoomError, RoomManager, RoomStatus
from Code.Server.user_manager import UserManager
from Code.Shared.message_types import MessageType


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.stopped = False

    def send_message(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    def stop(self) -> None:
        self.stopped = True


def login(users: UserManager, connection: FakeConnection, name: str) -> None:
    users.handle_message(connection, {"type": MessageType.JOIN.value, "name": name})  # type: ignore[arg-type]


def setup_players(*names: str):
    users = UserManager()
    rooms = RoomManager(users)
    connections = [FakeConnection() for _ in names]
    for connection, name in zip(connections, names):
        login(users, connection, name)
    return users, rooms, connections


def test_create_room_sets_host_x_and_waiting_status() -> None:
    _, rooms, (alice,) = setup_players("Alice")

    room = rooms.create_room(alice)

    assert room.status is RoomStatus.WAITING
    assert room.host == "Alice"
    assert [(seat.player_name, seat.piece) for seat in room.players] == [("Alice", Piece.X)]
    assert rooms.get_rooms() == [room]


def test_join_room_assigns_o_and_marks_room_ready() -> None:
    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    waiting = rooms.create_room(alice)

    ready = rooms.join_room(bob, waiting.room_id)

    assert ready.status is RoomStatus.READY
    assert [(seat.player_name, seat.piece) for seat in ready.players] == [
        ("Alice", Piece.X),
        ("Bob", Piece.O),
    ]


def test_room_rejects_a_third_player_and_players_cannot_join_two_rooms() -> None:
    _, rooms, (alice, bob, carol) = setup_players("Alice", "Bob", "Carol")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)

    with pytest.raises(RoomError, match="full"):
        rooms.join_room(carol, room.room_id)
    with pytest.raises(RoomError, match="already in a room"):
        rooms.create_room(alice)


def test_leave_room_returns_to_waiting_and_promotes_host_when_needed() -> None:
    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)

    waiting = rooms.leave_room(alice, room.room_id)

    assert waiting is not None
    assert waiting.status is RoomStatus.WAITING
    assert waiting.host == "Bob"
    assert [(seat.player_name, seat.piece) for seat in waiting.players] == [("Bob", Piece.X)]
    assert rooms.leave_room(bob, room.room_id) is None
    assert rooms.get_room(room.room_id) is None


def test_remaining_player_can_leave_after_opponent_ends_match_by_leaving() -> None:
    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)

    rooms.leave_room(alice, room.room_id)
    rooms.handle_message(
        bob,
        {"type": MessageType.LEAVE_ROOM.value, "room_id": room.room_id},
    )

    assert bob.messages[-1] == {
        "type": MessageType.ROOM_UPDATE.value,
        "room_id": room.room_id,
        "status": "LEFT",
    }
    assert rooms.create_room(bob).status is RoomStatus.WAITING


def test_only_host_can_start_and_two_players_are_required() -> None:
    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    room = rooms.create_room(alice)

    with pytest.raises(RoomError, match="two players"):
        rooms.start_match(alice, room.room_id)

    rooms.join_room(bob, room.room_id)
    with pytest.raises(RoomError, match="host"):
        rooms.start_match(bob, room.room_id)

    started = rooms.start_match(alice, room.room_id)
    assert started.status is RoomStatus.IN_PROGRESS
    assert started.game_state is GameState.IN_PROGRESS
    assert started.board is not None


def test_moves_require_membership_and_engine_enforces_turns() -> None:
    _, rooms, (alice, bob, carol) = setup_players("Alice", "Bob", "Carol")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)

    with pytest.raises(RoomError, match="not a member"):
        rooms.make_move(carol, room.room_id, 7, 7)
    with pytest.raises(RoomError, match="Invalid move"):
        rooms.make_move(bob, room.room_id, 7, 7)

    after_move = rooms.make_move(alice, room.room_id, 7, 7)
    assert after_move.current_player is Piece.O
    assert after_move.board[7][7] == Piece.X.value
    alice.messages.clear()
    assert rooms.send_current_state(alice, room.room_id) is True
    assert alice.messages[-1]["version"] == rooms.synchronization.get_version(room.room_id)
    assert alice.messages[-1]["authority"] == "SERVER"
    assert rooms.send_current_state(carol, room.room_id) is False


def test_disconnect_during_match_pauses_until_player_reconnects() -> None:
    users, rooms, (alice, bob) = setup_players("Alice", "Bob")
    token = bob.messages[-1]["resume_token"]
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)

    paused = rooms.disconnect(bob)
    users.disconnect(bob)

    assert paused is not None
    assert paused.status is RoomStatus.PAUSED
    assert paused.game_state is GameState.IN_PROGRESS
    replacement = FakeConnection()
    users.handle_message(
        replacement,
        {"type": MessageType.JOIN.value, "name": "Bob", "resume_token": token},
    )  # type: ignore[arg-type]
    resumed = rooms.get_room(room.room_id)
    assert resumed is not None and resumed.status is RoomStatus.IN_PROGRESS
    assert resumed.current_player is Piece.X
    assert rooms.make_move(alice, room.room_id, 7, 7).history[0]["player"] == "Alice"


def test_engine_win_ends_room_and_releases_both_players() -> None:
    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)

    for col in range(5):
        ended = rooms.make_move(alice, room.room_id, 7, col)
        if col < 4:
            rooms.make_move(bob, room.room_id, 0, col)

    assert ended.status is RoomStatus.ENDED
    assert ended.game_state is GameState.X_WON
    assert rooms.create_room(alice).status is RoomStatus.WAITING
    assert rooms.create_room(bob).status is RoomStatus.WAITING


def test_room_actions_require_authenticated_available_user() -> None:
    users = UserManager()
    rooms = RoomManager(users)
    anonymous = FakeConnection()

    with pytest.raises(RoomError, match="Login is required"):
        rooms.create_room(anonymous)

    alice = FakeConnection()
    login(users, alice, "Alice")
    bob = FakeConnection()
    login(users, bob, "Bob")
    users.handle_message(alice, {"type": MessageType.INVITE.value, "target": "Bob"})  # type: ignore[arg-type]

    with pytest.raises(RoomError, match="not available"):
        rooms.create_room(alice)


def test_lobby_invite_cannot_make_an_in_match_player_unavailable() -> None:
    users, rooms, (alice, bob, carol) = setup_players("Alice", "Bob", "Carol")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)

    users.handle_message(carol, {"type": MessageType.INVITE.value, "target": "Alice"})  # type: ignore[arg-type]

    assert carol.messages[-1]["type"] == MessageType.ERROR.value
    assert rooms.make_move(alice, room.room_id, 7, 7).current_player is Piece.O


def test_concurrent_moves_publish_room_snapshots_in_commit_order(monkeypatch) -> None:
    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)
    first_publish_entered = threading.Event()
    release_first_publish = threading.Event()
    second_publish_entered = threading.Event()
    published_piece_counts: list[int] = []
    original_notify = rooms._notify_room

    def delayed_notify(snapshot, recipients):
        piece_count = sum(piece != Piece.EMPTY.value for row in snapshot.board for piece in row)
        if piece_count == 1:
            first_publish_entered.set()
            assert release_first_publish.wait(timeout=2.0)
        elif piece_count == 2:
            second_publish_entered.set()
        published_piece_counts.append(piece_count)
        original_notify(snapshot, recipients)

    monkeypatch.setattr(rooms, "_notify_room", delayed_notify)
    first_thread = threading.Thread(target=rooms.make_move, args=(alice, room.room_id, 7, 0))
    second_thread = threading.Thread(target=rooms.make_move, args=(bob, room.room_id, 0, 0))
    first_thread.start()
    try:
        assert first_publish_entered.wait(timeout=1.0)
        second_thread.start()
        assert not second_publish_entered.wait(timeout=0.2)
    finally:
        release_first_publish.set()
        first_thread.join(timeout=2.0)
        if second_thread.ident is not None:
            second_thread.join(timeout=2.0)

    assert published_piece_counts == [1, 2]
    assert rooms.get_room(room.room_id).current_player is Piece.X