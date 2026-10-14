"""Tk tests for board rendering and server-authoritative outcomes."""

import tkinter as tk
from types import SimpleNamespace

from Code.Client.game_view import GameView


def test_game_view_emits_coordinates_but_does_not_change_authoritative_state(tk_root: tk.Tk) -> None:
    root = tk_root
    moves: list[tuple[str, int, int]] = []
    view = GameView(root, lambda room_id, row, col: moves.append((room_id, row, col)))
    view.pack()
    state = {
        "room_id": "room-1",
        "status": "IN_PROGRESS",
        "game_state": "IN_PROGRESS",
        "current_player": "X",
        "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
        "board": [[" " for _ in range(15)] for _ in range(15)],
    }
    view.update_state(state, "Alice")
    view._handle_board_click(SimpleNamespace(x=28 + 4 * 36, y=28 + 7 * 36))

    assert moves == [("room-1", 7, 4)]
    assert state["board"][7][4] == " "
    assert view.turn_var.get() == "Your turn  /  X"

    state["status"] = "ENDED"
    state["game_state"] = "O_WON"
    state["board"][7][4] = "X"
    view.update_state(state, "Alice")
    view._handle_board_click(SimpleNamespace(x=28 + 5 * 36, y=28 + 7 * 36))

    assert moves == [("room-1", 7, 4)]
    assert view.result_var.get() == "😢 BẠN ĐÃ THUA!"


def test_game_view_does_not_send_move_when_it_is_the_opponents_turn(tk_root: tk.Tk) -> None:
    moves: list[tuple[str, int, int]] = []
    view = GameView(tk_root, lambda room_id, row, col: moves.append((room_id, row, col)))
    view.pack()
    view.update_state(
        {
            "room_id": "room-1",
            "status": "IN_PROGRESS",
            "current_player": "O",
            "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
            "board": [[" " for _ in range(15)] for _ in range(15)],
        },
        "Alice",
    )

    view._handle_board_click(SimpleNamespace(x=28 + 4 * 36, y=28 + 7 * 36))

    assert moves == []
    assert view.turn_var.get() == "Opponent's turn  /  O"


def test_click_and_piece_alignment_top_left_center_bottom_right(tk_root: tk.Tk) -> None:
    moves: list[tuple[str, int, int]] = []
    view = GameView(tk_root, lambda room_id, row, col: moves.append((room_id, row, col)))
    view.pack()

    board = [[" " for _ in range(15)] for _ in range(15)]
    state = {
        "room_id": "room-1",
        "status": "IN_PROGRESS",
        "game_state": "IN_PROGRESS",
        "current_player": "X",
        "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
        "board": board,
    }
    view.update_state(state, "Alice")

    # 1. Top-left cell: row 0, col 0. Center is (28 + 18, 28 + 18) = (46, 46)
    view._handle_board_click(SimpleNamespace(x=46, y=46))
    assert moves[-1] == ("room-1", 0, 0)

    # 2. Center cell: row 7, col 7. Center is (28 + 7*36 + 18, 28 + 7*36 + 18) = (298, 298)
    view._handle_board_click(SimpleNamespace(x=298, y=298))
    assert moves[-1] == ("room-1", 7, 7)

    # 3. Bottom-right cell: row 14, col 14. Center is (28 + 14*36 + 18, 28 + 14*36 + 18) = (550, 550)
    view._handle_board_click(SimpleNamespace(x=550, y=550))
    assert moves[-1] == ("room-1", 14, 14)

    # Verify rendering centering for X and O
    board[0][0] = "X"
    board[7][7] = "O"
    board[14][14] = "X"
    view.update_state(state, "Alice")

    # Check drawn pieces centers
    # 32 grid lines are the first 32 items
    all_items = view.canvas.find_all()
    # 32 grid lines + 2 lines for X at (0,0) + 1 oval for O at (7,7) + 2 lines for X at (14,14) = 37 items
    assert len(all_items) == 37

    # X at (0, 0) - lines 33 and 34 (1-indexed item IDs 33, 34)
    x1_line1 = view.canvas.coords(all_items[32])
    x1_line2 = view.canvas.coords(all_items[33])
    center_x0 = (x1_line1[0] + x1_line1[2]) / 2
    center_y0 = (x1_line1[1] + x1_line1[3]) / 2
    assert center_x0 == 46.0
    assert center_y0 == 46.0
    assert (x1_line2[0] + x1_line2[2]) / 2 == 46.0
    assert (x1_line2[1] + x1_line2[3]) / 2 == 46.0

    # O at (7, 7) - item 35 is oval
    oval_coords = view.canvas.coords(all_items[34])
    center_x7 = (oval_coords[0] + oval_coords[2]) / 2
    center_y7 = (oval_coords[1] + oval_coords[3]) / 2
    assert center_x7 == 298.0
    assert center_y7 == 298.0

    # X at (14, 14) - lines 36 and 37
    x14_line1 = view.canvas.coords(all_items[35])
    center_x14 = (x14_line1[0] + x14_line1[2]) / 2
    center_y14 = (x14_line1[1] + x14_line1[3]) / 2
    assert center_x14 == 550.0
    assert center_y14 == 550.0


def test_two_clients_display_pieces_at_identical_positions(tk_root: tk.Tk) -> None:
    view1 = GameView(tk_root, lambda r, row, col: None)
    view2 = GameView(tk_root, lambda r, row, col: None)

    board = [[" " for _ in range(15)] for _ in range(15)]
    board[0][0] = "X"
    board[7][7] = "O"
    board[14][14] = "X"

    state = {
        "room_id": "room-1",
        "status": "IN_PROGRESS",
        "game_state": "IN_PROGRESS",
        "current_player": "O",
        "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
        "board": board,
    }

    view1.update_state(state, "Alice")
    view2.update_state(state, "Bob")

    items1 = view1.canvas.find_all()
    items2 = view2.canvas.find_all()
    assert len(items1) == len(items2)

    for id1, id2 in zip(items1, items2, strict=True):
        assert view1.canvas.type(id1) == view2.canvas.type(id2)
        assert view1.canvas.coords(id1) == view2.canvas.coords(id2)


def test_occupied_cell_and_out_of_bounds_click_ignored(tk_root: tk.Tk) -> None:
    moves: list[tuple[str, int, int]] = []
    view = GameView(tk_root, lambda room_id, row, col: moves.append((room_id, row, col)))
    view.pack()

    board = [[" " for _ in range(15)] for _ in range(15)]
    board[0][0] = "X"
    state = {
        "room_id": "room-1",
        "status": "IN_PROGRESS",
        "game_state": "IN_PROGRESS",
        "current_player": "X",
        "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
        "board": board,
    }
    view.update_state(state, "Alice")

    # Click on occupied cell (0, 0)
    view._handle_board_click(SimpleNamespace(x=46, y=46))
    assert moves == []

    # Click outside board bounds (margin area)
    view._handle_board_click(SimpleNamespace(x=10, y=10))
    view._handle_board_click(SimpleNamespace(x=580, y=580))
    assert moves == []
