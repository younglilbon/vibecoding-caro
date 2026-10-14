"""End-to-end GUI-to-server checks using two real NetworkClient workers."""

import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from Code.Client.app import App
from Code.Server.room_manager import RoomManager
from Code.Server.server import TCPServer
from Code.Server.user_manager import UserManager


def wait_for(root: tk.Tk, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    root.update()
    assert predicate(), "Timed out waiting for client/server state"


def test_two_tk_clients_login_join_play_and_detect_server_disconnect(tk_root: tk.Tk) -> None:
    users = UserManager()
    rooms = RoomManager(users)

    def on_message(connection, message):
        if not users.handle_message(connection, message):
            rooms.handle_message(connection, message)

    def on_disconnect(connection):
        player = users.get_player(connection)
        users.disconnect(connection)
        rooms.disconnect(connection, player_name=player.name if player is not None else None)

    server = TCPServer(
        "127.0.0.1",
        0,
        client_timeout=10.0,
        on_message=on_message,
        on_disconnect=on_disconnect,
    )
    host, port = server.start()
    second_window = tk.Toplevel(tk_root)
    second_window.withdraw()
    first_app = App(tk_root)
    second_app = App(second_window)
    try:
        assert first_app.login_view is not None
        assert second_app.login_view is not None

        first_app.login_view.name_var.set("Alice")
        first_app.login_view.host_var.set(host)
        first_app.login_view.port_var.set(str(port))
        first_app.login_view.submit()
        second_app.login_view.name_var.set("Bob")
        second_app.login_view.host_var.set(host)
        second_app.login_view.port_var.set(str(port))
        second_app.login_view.submit()

        wait_for(
            tk_root,
            lambda: first_app.lobby_view is not None
            and second_app.lobby_view is not None
            and first_app.connection_var.get() == "CONNECTED"
            and second_app.connection_var.get() == "CONNECTED",
        )
        assert first_app.player_name == "Alice"
        assert second_app.player_name == "Bob"

        first_app._create_room()
        wait_for(tk_root, lambda: first_app.room_view is not None)
        assert first_app.room_view is not None
        room_id = first_app.active_room_id
        assert room_id is not None
        second_app._refresh_rooms()
        wait_for(
            tk_root,
            lambda: second_app.lobby_view is not None
            and room_id in second_app.lobby_view._rooms,
        )
        second_app._join_room(room_id)
        wait_for(
            tk_root,
            lambda: first_app.room_view is not None
            and second_app.room_view is not None
            and first_app.room_view.status_var.get() == "READY"
            and second_app.room_view.status_var.get() == "READY",
        )

        first_app._start_match(room_id)
        wait_for(
            tk_root,
            lambda: first_app.room_view is not None
            and second_app.room_view is not None
            and first_app.room_view.status_var.get() == "IN PROGRESS"
            and second_app.room_view.status_var.get() == "IN PROGRESS",
        )
        first_board = first_app.room_view.game_view
        second_board = second_app.room_view.game_view
        assert len(first_board._board) == len(second_board._board) == 15
        assert all(len(row) == 15 for row in first_board._board)
        initial_grid_lines = sum(
            first_board.canvas.type(item_id) == "line"
            for item_id in first_board.canvas.find_all()
        )
        assert initial_grid_lines == 32

        first_board._handle_board_click(
            SimpleNamespace(
                x=first_board.MARGIN + 5 * first_board.CELL_SIZE,
                y=first_board.MARGIN + 6 * first_board.CELL_SIZE,
            )
        )
        wait_for(
            tk_root,
            lambda: second_app.room_view is not None
            and first_app.room_view is not None
            and second_app.room_view.game_view._board[6][5] == "X"
            and first_app.room_view.game_view._board[6][5] == "X",
        )
        assert first_app.room_view.game_view._board == second_app.room_view.game_view._board
        assert second_app.room_view.game_view.turn_var.get() == "Your turn  /  O"

        version_after_x = rooms.synchronization.get_version(room_id)
        first_board._handle_board_click(
            SimpleNamespace(
                x=first_board.MARGIN + 6 * first_board.CELL_SIZE,
                y=first_board.MARGIN + 6 * first_board.CELL_SIZE,
            )
        )
        wait_for(tk_root, lambda: first_app.room_view is not None)
        assert rooms.synchronization.get_version(room_id) == version_after_x
        assert first_app.room_view.game_view._board[6][6] == " "

        second_app._leave_room(room_id)
        wait_for(
            tk_root,
            lambda: second_app.lobby_view is not None
            and first_app.lobby_view is not None
            and second_app.room_view is None
            and first_app.room_view is None
            and room_id in first_app.lobby_view._rooms,
        )
        assert first_app.lobby_view._rooms[room_id]["status"] == "ENDED"
        first_app._create_room()
        wait_for(
            tk_root,
            lambda: first_app.room_view is not None
            and first_app.room_view.status_var.get() == "WAITING",
        )
        second_app._logout()
        wait_for(
            tk_root,
            lambda: second_app.login_view is not None
            and second_app.connection_var.get() == "DISCONNECTED",
        )

        server.stop()
        wait_for(
            tk_root,
            lambda: first_app.connection_var.get() == "DISCONNECTED",
        )
        assert first_app.room_view is not None
        assert "Server" in first_app.room_view.status_var.get()
    finally:
        first_app.stop()
        second_app.stop()
        second_window.destroy()
        server.stop()