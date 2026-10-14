"""Tests for reconnect, timer, spectator, chat, replay, and match-history behavior."""

import time

import pytest

from Code.Server.room_manager import RoomError, RoomManager, RoomStatus
from Code.Server.match_history_store import MatchHistoryStore
from Code.Server.user_manager import UserManager
from Code.Shared.message_types import MessageType


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def send_message(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    def stop(self) -> None:
        pass


def login(users: UserManager, name: str) -> FakeConnection:
    connection = FakeConnection()
    users.handle_message(
        connection,
        {"type": MessageType.JOIN.value, "name": name},  # type: ignore[arg-type]
    )
    assert users.get_player(connection) is not None  # type: ignore[arg-type]
    return connection


def active_match(
    *, reconnect_timeout: float = 60.0, turn_timeout: float = 30.0
) -> tuple[UserManager, RoomManager, FakeConnection, FakeConnection, str]:
    users = UserManager(reconnect_timeout=reconnect_timeout)
    rooms = RoomManager(users, turn_timeout=turn_timeout)
    alice = login(users, "Alice")
    bob = login(users, "Bob")
    room = rooms.create_room(alice)  # type: ignore[arg-type]
    rooms.join_room(bob, room.room_id)  # type: ignore[arg-type]
    rooms.start_match(alice, room.room_id)  # type: ignore[arg-type]
    return users, rooms, alice, bob, room.room_id


def test_resume_token_restores_paused_game_and_invalid_token_is_rejected() -> None:
    users, rooms, alice, bob, room_id = active_match()
    token = bob.messages[0]["resume_token"]
    rooms.make_move(alice, room_id, 5, 6)  # type: ignore[arg-type]
    before = rooms.get_room(room_id)
    rooms.disconnect(bob)  # type: ignore[arg-type]
    users.disconnect(bob)  # type: ignore[arg-type]
    assert rooms.get_room(room_id).status is RoomStatus.PAUSED

    impostor = FakeConnection()
    users.handle_message(
        impostor,
        {
            "type": MessageType.JOIN.value,
            "name": "Bob",
            "resume_token": "invalid-token",
        },  # type: ignore[arg-type]
    )
    assert impostor.messages[-1]["type"] == MessageType.ERROR.value

    replacement = FakeConnection()
    users.handle_message(
        replacement,
        {
            "type": MessageType.JOIN.value,
            "name": "Bob",
            "resume_token": token,
        },  # type: ignore[arg-type]
    )
    resumed = rooms.get_room(room_id)
    assert resumed is not None
    assert resumed.status is RoomStatus.IN_PROGRESS
    assert resumed.board == before.board
    assert resumed.current_player == before.current_player
    assert resumed.history == before.history


def test_reconnect_expiry_ends_match_with_connected_player_as_winner() -> None:
    users, rooms, _, bob, room_id = active_match(reconnect_timeout=0.04)
    rooms.disconnect(bob)  # type: ignore[arg-type]
    users.disconnect(bob)  # type: ignore[arg-type]

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        room = rooms.get_room(room_id)
        if room is not None and room.status is RoomStatus.ENDED:
            break
        time.sleep(0.01)

    assert room is not None and room.status is RoomStatus.ENDED
    assert room.winner == "Alice"
    assert room.loser == "Bob"
    assert room.reason and "DISCONNECT_TIMEOUT" in room.reason
    assert rooms._rooms[room_id].turn_timer is None
    assert users.get_player(bob) is None  # type: ignore[arg-type]


def test_server_turn_timeout_and_invalid_moves_do_not_change_history() -> None:
    _, rooms, alice, bob, room_id = active_match(turn_timeout=0.07)
    room = rooms.get_room(room_id)
    assert room is not None and room.remaining_seconds is not None
    remaining_before = room.remaining_seconds

    with pytest.raises(RoomError):
        rooms.make_move(bob, room_id, 0, 0)  # type: ignore[arg-type]
    room_after_invalid = rooms.get_room(room_id)
    assert room_after_invalid is not None
    assert room_after_invalid.history == ()
    assert room_after_invalid.current_player == room.current_player
    assert room_after_invalid.remaining_seconds <= remaining_before

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        room_after_timeout = rooms.get_room(room_id)
        if room_after_timeout is not None and room_after_timeout.status is RoomStatus.ENDED:
            break
        time.sleep(0.01)
    assert room_after_timeout is not None
    assert room_after_timeout.status is RoomStatus.ENDED
    assert room_after_timeout.winner == "Bob"
    assert room_after_timeout.reason and "TIMEOUT" in room_after_timeout.reason


def test_spectator_is_read_only_and_can_chat_but_not_move_or_surrender() -> None:
    users, rooms, alice, bob, room_id = active_match()
    rooms.make_move(alice, room_id, 5, 6)  # type: ignore[arg-type]
    before_watch = rooms.get_room(room_id)
    assert before_watch is not None
    viewer = login(users, "Viewer")
    rooms.watch_room(viewer, room_id)  # type: ignore[arg-type]
    state = rooms.get_room(room_id)
    assert state is not None and state.spectators == ("Viewer",)
    initial_state = next(
        message for message in reversed(viewer.messages)
        if message.get("type") == MessageType.ROOM_UPDATE.value
    )
    assert initial_state["status"] == RoomStatus.IN_PROGRESS.value
    assert initial_state["board"] == [list(row) for row in before_watch.board]
    assert initial_state["history"] == list(before_watch.history)
    assert initial_state["current_player"] == before_watch.current_player.value
    assert initial_state["remaining_seconds"] <= before_watch.remaining_seconds
    with pytest.raises(RoomError):
        rooms.make_move(viewer, room_id, 0, 0)  # type: ignore[arg-type]
    with pytest.raises(RoomError, match="seated players"):
        rooms.surrender(viewer, room_id)  # type: ignore[arg-type]

    rooms.send_chat(viewer, room_id, "  hello room  ")  # type: ignore[arg-type]
    chat = [message for message in viewer.messages if message.get("type") == MessageType.CHAT.value]
    assert chat[-1]["content"] == "hello room"
    assert chat[-1]["sender"] == "Viewer"
    assert rooms.get_room(room_id).current_player == state.current_player
    rooms.make_move(bob, room_id, 3, 3)  # type: ignore[arg-type]
    live_update = next(
        message for message in reversed(viewer.messages)
        if message.get("type") == MessageType.ROOM_UPDATE.value
    )
    assert live_update["history"][-1]["player"] == "Bob"
    assert live_update["board"][3][3] == "O"
    assert live_update["remaining_seconds"] <= rooms.turn_timeout
    after_update = rooms.get_room(room_id)
    assert after_update is not None
    with pytest.raises(RoomError):
        rooms.send_chat(viewer, room_id, " " * 501)  # type: ignore[arg-type]
    with pytest.raises(RoomError):
        rooms.send_chat(viewer, "another-room", "hello")  # type: ignore[arg-type]
    assert rooms._room_by_connection.get(viewer) is None  # type: ignore[arg-type]
    rooms.leave_spectator(viewer)  # type: ignore[arg-type]
    after_leave = rooms.get_room(room_id)
    assert after_leave is not None
    assert after_leave.status is RoomStatus.IN_PROGRESS
    assert after_leave.spectators == ()
    assert after_leave.board == after_update.board
    assert after_leave.current_player == after_update.current_player


def test_completed_match_history_survives_reopening_sqlite_store(tmp_path) -> None:
    database = tmp_path / "matches.sqlite3"
    store = MatchHistoryStore(database)
    users = UserManager()
    rooms = RoomManager(users, history_store=store)
    alice = login(users, "Alice")
    bob = login(users, "Bob")
    room = rooms.create_room(alice)  # type: ignore[arg-type]
    rooms.join_room(bob, room.room_id)  # type: ignore[arg-type]
    rooms.start_match(alice, room.room_id)  # type: ignore[arg-type]
    rooms.make_move(alice, room.room_id, 4, 5)  # type: ignore[arg-type]
    rooms.make_move(bob, room.room_id, 3, 3)  # type: ignore[arg-type]
    ended = rooms.surrender(alice, room.room_id)  # type: ignore[arg-type]

    persisted = MatchHistoryStore(database).get_completed_matches()

    assert len(persisted) == 1
    result = persisted[0]
    assert result["room_id"] == room.room_id
    assert result["round_number"] == 1
    assert result["players"] == [
        {"name": "Alice", "piece": "X"},
        {"name": "Bob", "piece": "O"},
    ]
    assert result["winner"] == ended.winner == "Bob"
    assert result["loser"] == ended.loser == "Alice"
    assert result["game_state"] == "O_WON"
    assert result["winning_piece"] == "O"
    assert result["reason"] == "SURRENDER"
    assert [
        (move["player"], move["row"], move["col"])
        for move in result["moves"]
    ] == [("Alice", 4, 5), ("Bob", 3, 3)]
    assert all(move["timestamp"] for move in result["moves"])
    assert result["started_at"] and result["ended_at"]


def test_spectator_limit_is_ten() -> None:
    users, rooms, _, _, room_id = active_match()
    for index in range(10):
        viewer = login(users, f"Viewer{index:02d}")
        rooms.watch_room(viewer, room_id)  # type: ignore[arg-type]
    extra = login(users, "ExtraViewer")
    with pytest.raises(RoomError, match="limit"):
        rooms.watch_room(extra, room_id)  # type: ignore[arg-type]


def test_surrender_history_replay_and_room_list_metadata() -> None:
    _, rooms, alice, bob, room_id = active_match()
    rooms.make_move(alice, room_id, 4, 5)  # type: ignore[arg-type]
    rooms.make_move(bob, room_id, 3, 3)  # type: ignore[arg-type]
    live = rooms.get_room(room_id)
    assert live is not None
    assert [move["number"] for move in live.history] == [1, 2]
    assert live.history[0]["timestamp"]

    ended = rooms.surrender(alice, room_id)  # type: ignore[arg-type]
    assert ended.status is RoomStatus.ENDED
    assert ended.winner == "Bob" and ended.loser == "Alice"
    assert ended.reason and "SURRENDER" in ended.reason
    assert rooms._rooms[room_id].turn_timer is None

    rooms.request_replay(alice, room_id, True)  # type: ignore[arg-type]
    waiting = rooms.get_room(room_id)
    assert waiting is not None and waiting.status is RoomStatus.ENDED
    rooms.request_replay(bob, room_id, True)  # type: ignore[arg-type]
    restarted = rooms.get_room(room_id)
    assert restarted is not None and restarted.status is RoomStatus.IN_PROGRESS
    assert restarted.current_player.value == "X"
    assert all(piece == " " for row in restarted.board for piece in row)
    assert len(restarted.history) == 2
    assert restarted.history[-1]["round"] == 1

    summaries = rooms.get_rooms(include_ended=True)
    assert summaries[0].room_id == room_id
