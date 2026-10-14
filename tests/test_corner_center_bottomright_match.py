"""Integration check for top-left, center, and bottom-right moves and alignment."""

import time
import tkinter as tk
from types import SimpleNamespace

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


def test_top_left_center_bottom_right_real_match(tk_root: tk.Tk) -> None:
    users = UserManager()
    rooms = RoomManager(users)

    def on_message(connection, message):
        if not users.handle_message(connection, message):
            rooms.handle_message(connection, message)

    def on_disconnect(connection):
        rooms.disconnect(connection)
        users.disconnect(connection)

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

        first_app._create_room()
        wait_for(tk_root, lambda: first_app.room_view is not None)
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

        # Check grid lines: 16 horizontal + 16 vertical = 32 boundary lines
        grid_lines = sum(first_board.canvas.type(it) == "line" for it in first_board.canvas.find_all())
        assert grid_lines == 32

        # 1. Top-left click by Alice (X) at center of cell (0, 0): (46, 46)
        first_board._handle_board_click(SimpleNamespace(x=46, y=46))
        wait_for(
            tk_root,
            lambda: first_board._board is not None
            and second_board._board is not None
            and first_board._board[0][0] == "X"
            and second_board._board[0][0] == "X",
        )
        # Check turn switched to Bob (O)
        assert second_board.turn_var.get() == "Your turn  /  O"
        assert first_board.turn_var.get() == "Opponent's turn  /  O"

        # 2. Center click by Bob (O) at center of cell (7, 7): (298, 298)
        second_board._handle_board_click(SimpleNamespace(x=298, y=298))
        wait_for(
            tk_root,
            lambda: first_board._board[7][7] == "O"
            and second_board._board[7][7] == "O",
        )
        assert first_board.turn_var.get() == "Your turn  /  X"

        # 3. Bottom-right click by Alice (X) at center of cell (14, 14): (550, 550)
        first_board._handle_board_click(SimpleNamespace(x=550, y=550))
        wait_for(
            tk_root,
            lambda: first_board._board[14][14] == "X"
            and second_board._board[14][14] == "X",
        )

        # Verify items on both clients
        items1 = first_board.canvas.find_all()
        items2 = second_board.canvas.find_all()
        assert len(items1) == len(items2) == 37  # 32 lines + 2 lines for X + 1 oval for O + 2 lines for X

        for i1, i2 in zip(items1, items2, strict=True):
            assert first_board.canvas.type(i1) == second_board.canvas.type(i2)
            assert first_board.canvas.coords(i1) == second_board.canvas.coords(i2)

        # Check exact centers of the drawn pieces:
        # X at (0, 0)
        x0_coords = first_board.canvas.coords(items1[32])
        assert (x0_coords[0] + x0_coords[2]) / 2 == 46.0
        assert (x0_coords[1] + x0_coords[3]) / 2 == 46.0

        # O at (7, 7)
        o7_coords = first_board.canvas.coords(items1[34])
        assert (o7_coords[0] + o7_coords[2]) / 2 == 298.0
        assert (o7_coords[1] + o7_coords[3]) / 2 == 298.0

        # X at (14, 14)
        x14_coords = first_board.canvas.coords(items1[35])
        assert (x14_coords[0] + x14_coords[2]) / 2 == 550.0
        assert (x14_coords[1] + x14_coords[3]) / 2 == 550.0

    finally:
        first_app.stop()
        second_app.stop()
        second_window.destroy()
        server.stop()
