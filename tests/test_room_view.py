"""Tk tests for RoomView host controls and server-state rendering."""

import tkinter as tk

from Code.Client.room_view import RoomView


def test_room_view_exposes_host_actions_and_passes_server_state_to_game_view(tk_root: tk.Tk) -> None:
    root = tk_root
    actions: list[tuple] = []
    view = RoomView(
        root,
        "Alice",
        on_start=lambda room_id: actions.append(("start", room_id)),
        on_leave=lambda room_id: actions.append(("leave", room_id)),
        on_move=lambda room_id, row, col: actions.append(("move", room_id, row, col)),
        on_chat=lambda room_id, content: actions.append(("chat", room_id, content)),
        on_surrender=lambda room_id: actions.append(("surrender", room_id)),
        on_replay=lambda room_id, accepted: actions.append(("replay", room_id, accepted)),
    )
    view.pack()
    state = {
        "room_id": "room-123456",
        "host": "Alice",
        "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
        "status": "READY",
        "game_state": None,
        "current_player": None,
        "board": None,
    }
    view.update_state(state)

    assert str(view.start_button["state"]) == "normal"
    assert view.game_view.piece_var.get() == "Your piece: X"
    assert view.waiting_panel.winfo_manager() == "grid"
    assert view.game_view.winfo_manager() == ""
    view.start_button.invoke()
    view._leave()
    view.update_state({**state, "host": "Bob"})
    assert str(view.start_button["state"]) == "disabled"
    assert actions == [("start", "room-123456"), ("leave", "room-123456")]

    view.update_state({**state, "status": "IN_PROGRESS", "game_state": "IN_PROGRESS", "current_player": "X"})
    assert view.waiting_panel.winfo_manager() == ""
    assert view.game_view.winfo_manager() == "grid"


def test_room_view_chat_history_surrender_replay_and_review(tk_root: tk.Tk) -> None:
    actions: list[tuple] = []
    view = RoomView(
        tk_root,
        "Alice",
        on_start=lambda room_id: None,
        on_leave=lambda room_id: None,
        on_move=lambda room_id, row, col: None,
        on_chat=lambda room_id, content: actions.append(("chat", room_id, content)),
        on_surrender=lambda room_id: actions.append(("surrender", room_id)),
        on_replay=lambda room_id, accepted: actions.append(("replay", room_id, accepted)),
    )
    view.pack()
    board = [[" " for _ in range(15)] for _ in range(15)]
    board[2][3] = "X"
    state = {
        "room_id": "room-review",
        "host": "Alice",
        "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
        "status": "IN_PROGRESS",
        "game_state": "IN_PROGRESS",
        "current_player": "O",
        "board": board,
        "remaining_seconds": 27,
        "history": [
            {
                "number": 1,
                "round": 1,
                "player": "Alice",
                "piece": "X",
                "row": 2,
                "col": 3,
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        ],
    }
    view.update_state(state)
    assert view.game_view.clock_var.get() == "Turn time: 27s"
    view.surrender_button.invoke()
    view.chat_var.set("hello")
    view._submit_chat()
    view.history_list.selection_set(0)
    view._review_selected(None)  # type: ignore[arg-type]
    assert view.game_view._reviewing is True
    assert view.game_view._board[2][3] == "X"
    view._show_live_board()
    assert view.game_view._reviewing is False
    assert actions == [("surrender", "room-review"), ("chat", "room-review", "hello")]

    view.update_state({**state, "status": "ENDED", "game_state": "X_WON"})
    view.replay_button.invoke()
    assert actions[-1] == ("replay", "room-review", True)
