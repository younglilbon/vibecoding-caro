"""Tests for main-thread event routing and stale room-state rejection."""

import queue
import tkinter as tk
from typing import Any

from Code.Client.app import App
from Code.Client.network_client import NetworkEvent
from Code.Server.room_manager import RoomStatus
from Code.Shared.message_types import MessageType


class FakeNetworkClient:
    def __init__(self) -> None:
        self.connected = True
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def connect(self, _host: str, _port: int) -> None:
        raise AssertionError("An active connection must be reused for a login retry")

    def send(self, message: dict[str, Any]) -> bool:
        self.sent.append(message)
        return True

    def close(self) -> None:
        self.closed = True


def test_app_routes_login_and_ignores_stale_or_client_authority_updates(tk_root: tk.Tk) -> None:
    root = tk_root
    network = FakeNetworkClient()
    app = App(root, network_client=network)  # type: ignore[arg-type]
    try:
        app._pending_name = "Alice"
        app._join_sent = True
        app._handle_message({"type": MessageType.PLAYER_LIST.value, "players": ["Alice", "Bob"]})
        assert app.lobby_view is not None
        assert network.sent[-1] == {"type": MessageType.ROOM_LIST.value}

        app._pending_room_action = True
        first_state = {
            "type": MessageType.ROOM_UPDATE.value,
            "room_id": "room-1",
            "authority": "SERVER",
            "version": 4,
            "status": "IN_PROGRESS",
            "host": "Alice",
            "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
            "game_state": "IN_PROGRESS",
            "current_player": "X",
            "board": [[" ", "X"]],
        }
        app._handle_message(first_state)
        assert app.room_view is not None
        app._handle_message({**first_state, "version": 3, "board": [["O", "O"]]})
        app._handle_message({**first_state, "version": 5, "authority": "CLIENT", "board": [["O", "O"]]})

        assert app.room_view.game_view._board == [[" ", "X"]]
        assert app._room_versions["room-1"] == 4
    finally:
        app.stop()


def test_app_retries_join_over_the_existing_connection(tk_root: tk.Tk) -> None:
    root = tk_root
    network = FakeNetworkClient()
    app = App(root, network_client=network)  # type: ignore[arg-type]
    try:
        app._begin_login("Bob", "localhost", 12345)

        assert network.sent == [{"type": MessageType.JOIN.value, "name": "Bob"}]
    finally:
        app.stop()


def test_server_disconnect_is_visible_in_room_screen(tk_root: tk.Tk) -> None:
    network = FakeNetworkClient()
    app = App(tk_root, network_client=network)  # type: ignore[arg-type]
    try:
        app.player_name = "Alice"
        state = {
            "type": MessageType.ROOM_UPDATE.value,
            "room_id": "room-1",
            "authority": "SERVER",
            "version": 1,
            "status": RoomStatus.IN_PROGRESS.value,
            "host": "Alice",
            "players": [{"name": "Alice", "piece": "X"}],
            "game_state": "IN_PROGRESS",
            "current_player": "X",
            "board": [[" " for _ in range(15)] for _ in range(15)],
        }
        app._pending_room_action = True
        app._handle_message(state)
        assert app.room_view is not None

        app._handle_event(NetworkEvent("status", {"state": "DISCONNECTED", "reason": "Server stopped"}))

        assert app.connection_var.get() == "DISCONNECTED"
        assert app.room_view.status_var.get() == "Server stopped"
    finally:
        app.stop()


def test_player_left_event_returns_remaining_player_to_lobby(tk_root: tk.Tk) -> None:
    network = FakeNetworkClient()
    app = App(tk_root, network_client=network)  # type: ignore[arg-type]
    try:
        app.player_name = "Bob"
        app._logged_in = True
        app._pending_room_action = True
        app._handle_message(
            {
                "type": MessageType.ROOM_UPDATE.value,
                "room_id": "room-1",
                "authority": "SERVER",
                "version": 1,
                "event": "STATE",
                "status": "IN_PROGRESS",
                "host": "Alice",
                "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
                "game_state": "IN_PROGRESS",
                "current_player": "X",
                "board": [[" " for _ in range(15)] for _ in range(15)],
            }
        )
        assert app.room_view is not None

        app._handle_message(
            {
                "type": MessageType.ROOM_UPDATE.value,
                "room_id": "room-1",
                "authority": "SERVER",
                "version": 2,
                "event": "PLAYER_LEFT",
                "status": "ENDED",
                "host": "Alice",
                "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
            }
        )

        assert app.room_view is None
        assert app.lobby_view is not None
        assert app.active_room_id is None
        assert network.sent[-1] == {"type": MessageType.ROOM_LIST.value}
    finally:
        app.stop()


def test_lobby_logout_sends_leave_and_returns_to_login_on_disconnect(tk_root: tk.Tk) -> None:
    network = FakeNetworkClient()
    app = App(tk_root, network_client=network)  # type: ignore[arg-type]
    try:
        app.player_name = "Alice"
        app._logged_in = True
        app._show_lobby()
        app._logout()

        assert network.sent == [{"type": MessageType.LEAVE.value}]
        assert app.lobby_view is not None
        assert app.login_view is None

        app._handle_event(NetworkEvent("status", {"state": "DISCONNECTED", "reason": "Server closed"}))

        assert app.login_view is not None
        assert app.lobby_view is None
        assert app.player_name is None
        assert app.login_view.status_var.get() == "Disconnected"
    finally:
        app.stop()