"""Win / lose effect tests.

Covers:
  1. X wins horizontally — overlay + winning_line highlight shown.
  2. X wins vertically — overlay shown.
  3. X wins diagonal TL→BR — overlay shown.
  4. O wins diagonal TR→BL — overlay shown.
  5. Winner client sees CHIẾN THẮNG, loser sees BẠN ĐÃ THUA.
  6. Both clients display same result (board identical).
  7. No moves accepted after match ends (client and server).
  8. Overlay shown exactly once (no duplicate for same game_state).
  9. Server still handles other rooms after a win.
 10. apply_game_end() sets overlay from GAME_END message.
"""

import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from Code.Client.app import App
from Code.Client.game_view import GameView
from Code.Server.game_engine import GameEngine, GameState, Piece, Position
from Code.Server.room_manager import RoomError, RoomManager, RoomStatus
from Code.Server.server import TCPServer
from Code.Server.user_manager import UserManager


# ────────────────────────────────────────── helpers ────────────────────────

def wait_for(root: tk.Tk, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    root.update()
    assert predicate(), "Timed out waiting for client/server state"


def _make_state(
    game_state: str,
    board: list[list[str]] | None = None,
    current_player: str = "X",
    winner: str | None = None,
    loser: str | None = None,
    winning_piece: str | None = None,
    winning_line: list[list[int]] | None = None,
    reason: str | None = None,
    status: str | None = None,
) -> dict:
    b = board or [[" "] * 15 for _ in range(15)]
    return {
        "room_id": "room-1",
        "status": status or ("ENDED" if game_state in ("X_WON", "O_WON", "DRAW") else "IN_PROGRESS"),
        "game_state": game_state,
        "current_player": current_player,
        "players": [{"name": "Alice", "piece": "X"}, {"name": "Bob", "piece": "O"}],
        "board": b,
        "winner": winner,
        "loser": loser,
        "winning_piece": winning_piece,
        "winning_line": winning_line,
        "reason": reason,
    }


def _canvas_overlay_exists(view: GameView) -> bool:
    """True if the overlay rectangle/text is on the canvas."""
    for item in view.canvas.find_withtag("overlay"):
        return True
    return False


# ────────────────────────── PART 1: Game Engine winning_line ─────────────

@pytest.mark.parametrize(
    ("direction", "label"),
    [
        ((0, 1), "horizontal"),
        ((1, 0), "vertical"),
        ((1, 1), "diagonal-TL-BR"),
        ((1, -1), "diagonal-TR-BL"),
    ],
)
def test_engine_winning_line_all_directions(direction: tuple[int, int], label: str) -> None:
    """Engine populates winning_line with exactly 5 coords in every direction."""
    game = GameEngine()
    center_row, center_col = 7, 7
    dr, dc = direction
    for dist in (-2, -1, 1, 2):
        game.board[center_row + dist * dr][center_col + dist * dc] = Piece.X
    game.current_player = Piece.X

    ok, _ = game.make_move(Position(center_row, center_col), Piece.X)

    assert ok
    assert game.get_game_state() == GameState.X_WON
    line = game.get_winning_line()
    assert len(line) == 5, f"{label}: expected 5 winning coords, got {line}"
    # All cells in line must contain X
    for r, c in line:
        assert game.board[r][c] == Piece.X, f"{label}: ({r},{c}) is not X"


def test_engine_winning_line_o_diagonal_tr_bl() -> None:
    """O wins on diagonal TR→BL and winning_line contains O's coords."""
    game = GameEngine()
    center_row, center_col = 7, 7
    for dist in (-2, -1, 1, 2):
        game.board[center_row + dist][center_col - dist] = Piece.O
    game.current_player = Piece.O

    ok, _ = game.make_move(Position(center_row, center_col), Piece.O)

    assert ok
    assert game.get_game_state() == GameState.O_WON
    line = game.get_winning_line()
    assert len(line) == 5
    for r, c in line:
        assert game.board[r][c] == Piece.O


def test_engine_rejects_moves_after_win() -> None:
    """Server game engine refuses further moves once a winner is declared."""
    game = GameEngine()
    for col in range(4):
        game.board[7][col] = Piece.X
    ok, _ = game.make_move(Position(7, 4), Piece.X)
    assert ok
    assert game.get_game_state() == GameState.X_WON

    # Any further move must fail
    ok2, _ = game.make_move(Position(0, 0), Piece.O)
    assert not ok2


# ────────────────────────── PART 2: RoomManager end info ─────────────────

def _setup_rooms(names=("Alice", "Bob")):
    users = UserManager()
    rooms = RoomManager(users)
    clients = []
    for name in names:
        from tests.test_room_manager import FakeConnection  # type: ignore
        c = FakeConnection()
        users.join(c, name)
        clients.append(c)
    return users, rooms, clients


def test_room_manager_snapshot_contains_win_info() -> None:
    """make_move snapshot includes winner, loser, winning_piece, winning_line, reason."""
    from tests.test_room_manager import setup_players  # type: ignore

    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)

    # Place 4 X's manually, then make the winning 5th
    with rooms._lock:
        engine = rooms._rooms[room.room_id].engine
        for col in range(4):
            engine.board[7][col] = Piece.X

    result = rooms.make_move(alice, room.room_id, 7, 4)

    assert result.status is RoomStatus.ENDED
    assert result.game_state == GameState.X_WON
    assert result.winner == "Alice"
    assert result.loser == "Bob"
    assert result.winning_piece == "X"
    assert result.winning_line is not None and len(result.winning_line) == 5
    assert result.reason is not None and "Alice" in result.reason


def test_room_manager_sends_game_end_message_on_win() -> None:
    """After a winning move, both players receive a GAME_END network message."""
    from tests.test_room_manager import setup_players  # type: ignore

    _, rooms, (alice, bob) = setup_players("Alice", "Bob")
    room = rooms.create_room(alice)
    rooms.join_room(bob, room.room_id)
    rooms.start_match(alice, room.room_id)

    with rooms._lock:
        engine = rooms._rooms[room.room_id].engine
        for col in range(4):
            engine.board[7][col] = Piece.X

    rooms.make_move(alice, room.room_id, 7, 4)

    # Both alice and bob should have received a GAME_END message
    alice_game_end = next(
        (m for m in alice.messages if m.get("type") == "GAME_END"), None
    )
    bob_game_end = next(
        (m for m in bob.messages if m.get("type") == "GAME_END"), None
    )
    assert alice_game_end is not None, "Alice did not receive GAME_END"
    assert bob_game_end is not None, "Bob did not receive GAME_END"
    assert alice_game_end["winner"] == "Alice"
    assert alice_game_end["loser"] == "Bob"
    assert alice_game_end["winning_piece"] == "X"
    assert len(alice_game_end["winning_line"]) == 5
    assert alice_game_end == bob_game_end  # Both receive identical payload

    with pytest.raises(RoomError):
        rooms.make_move(alice, room.room_id, 7, 5)
    assert sum(m.get("type") == "GAME_END" for m in alice.messages) == 1
    assert sum(m.get("type") == "GAME_END" for m in bob.messages) == 1


# ────────────────────────── PART 3 & 4: Win / lose overlay ──────────────

def test_winner_sees_win_overlay(tk_root: tk.Tk) -> None:
    """Alice (X) sees 🎉 CHIẾN THẮNG! overlay after X_WON is received."""
    view = GameView(tk_root, lambda r, row, col: None)
    board = [[" "] * 15 for _ in range(15)]
    for col in range(5):
        board[7][col] = "X"
    winning_line = [[7, c] for c in range(5)]

    state = _make_state(
        "X_WON", board=board,
        winner="Alice", loser="Bob", winning_piece="X",
        winning_line=winning_line, reason="Player Alice (X) wins with 5 in a row!",
    )
    view.update_state(state, "Alice")
    tk_root.update()

    assert "CHIẾN THẮNG" in view.result_var.get()
    assert _canvas_overlay_exists(view)


def test_loser_sees_lose_overlay(tk_root: tk.Tk) -> None:
    """Bob (O) sees 😢 BẠN ĐÃ THUA! overlay after X_WON is received."""
    view = GameView(tk_root, lambda r, row, col: None)
    board = [[" "] * 15 for _ in range(15)]
    for col in range(5):
        board[7][col] = "X"
    winning_line = [[7, c] for c in range(5)]

    state = _make_state(
        "X_WON", board=board,
        winner="Alice", loser="Bob", winning_piece="X",
        winning_line=winning_line, reason="Player Alice (X) wins with 5 in a row!",
    )
    view.update_state(state, "Bob")
    tk_root.update()

    assert "BẠN ĐÃ THUA" in view.result_var.get()
    assert _canvas_overlay_exists(view)


def test_overlay_shown_exactly_once(tk_root: tk.Tk) -> None:
    """Calling update_state multiple times with the same game_state never
    duplicates the overlay tag."""
    view = GameView(tk_root, lambda r, row, col: None)
    board = [[" "] * 15 for _ in range(15)]
    for col in range(5):
        board[7][col] = "X"
    winning_line = [[7, c] for c in range(5)]
    state = _make_state(
        "X_WON", board=board,
        winner="Alice", loser="Bob", winning_piece="X",
        winning_line=winning_line,
    )

    view.update_state(state, "Alice")
    tk_root.update()
    overlay_count = len(view.canvas.find_withtag("overlay"))
    blink_after_id = view._blink_after_id
    confetti_after_id = view._confetti_after_id

    for _ in range(4):
        view.update_state(state, "Alice")
        tk_root.update()

    overlay_items = list(view.canvas.find_withtag("overlay"))
    # Overlay renders a rectangle + text items; check it is exactly 1 "round"
    # The key guard is _overlay_shown_for — verify at most the first trigger fired
    assert view._overlay_shown_for == "X_WON"
    assert len(overlay_items) == overlay_count > 0
    assert view._blink_after_id == blink_after_id
    assert view._confetti_after_id == confetti_after_id


def test_no_moves_after_game_ended_client_side(tk_root: tk.Tk) -> None:
    """Client stops emitting moves once status is ENDED."""
    moves: list = []
    view = GameView(tk_root, lambda r, row, col: moves.append((r, row, col)))

    board = [[" "] * 15 for _ in range(15)]
    state_in_progress = _make_state(
        "IN_PROGRESS", board=board, status="IN_PROGRESS",
    )
    view.update_state(state_in_progress, "Alice")
    view._handle_board_click(SimpleNamespace(x=46, y=46))
    assert len(moves) == 1

    # Now the game ends
    for col in range(5):
        board[7][col] = "X"
    state_ended = _make_state(
        "X_WON", board=board,
        winner="Alice", loser="Bob", winning_piece="X",
        winning_line=[[7, c] for c in range(5)],
    )
    view.update_state(state_ended, "Alice")

    # Clicks must be ignored
    view._handle_board_click(SimpleNamespace(x=100, y=100))
    view._handle_board_click(SimpleNamespace(x=298, y=298))
    assert len(moves) == 1


def test_apply_game_end_sets_overlay(tk_root: tk.Tk) -> None:
    """apply_game_end() (called for GAME_END network msg) shows the overlay."""
    view = GameView(tk_root, lambda r, row, col: None)
    board = [[" "] * 15 for _ in range(15)]
    for col in range(5):
        board[7][col] = "X"
    state = _make_state("IN_PROGRESS", board=board, status="IN_PROGRESS")
    view.update_state(state, "Bob")

    view.apply_game_end({
        "type": "GAME_END",
        "room_id": "room-1",
        "game_state": "X_WON",
        "winner": "Alice",
        "loser": "Bob",
        "winning_piece": "X",
        "winning_line": [[7, c] for c in range(5)],
        "reason": "Player Alice (X) wins with 5 in a row!",
    })
    tk_root.update()

    assert view._overlay_shown_for == "X_WON"
    assert _canvas_overlay_exists(view)
    assert "BẠN ĐÃ THUA" in view.result_var.get()


def test_both_clients_identical_result_display(tk_root: tk.Tk) -> None:
    """Both clients receive the same ROOM_UPDATE and show the same board."""
    view_alice = GameView(tk_root, lambda r, row, col: None)
    view_bob = GameView(tk_root, lambda r, row, col: None)

    board = [[" "] * 15 for _ in range(15)]
    for col in range(5):
        board[7][col] = "X"
    state = _make_state(
        "X_WON", board=board,
        winner="Alice", loser="Bob", winning_piece="X",
        winning_line=[[7, c] for c in range(5)],
        reason="Player Alice (X) wins with 5 in a row!",
    )

    view_alice.update_state(state, "Alice")
    view_bob.update_state(state, "Bob")
    tk_root.update()

    assert view_alice._game_state == view_bob._game_state == "X_WON"
    assert view_alice._winner == view_bob._winner == "Alice"
    assert view_alice._winning_line == view_bob._winning_line
    assert "CHIẾN THẮNG" in view_alice.result_var.get()
    assert "BẠN ĐÃ THUA" in view_bob.result_var.get()


# ─────────────────── PART 7: End-to-end with real server ─────────────────

def test_e2e_x_wins_horizontal_both_clients_notified(tk_root: tk.Tk) -> None:
    """Full real-match test: X wins horizontally; both clients notified, board locked."""
    users = UserManager()
    rooms = RoomManager(users)

    def on_message(conn, msg):
        if not users.handle_message(conn, msg):
            rooms.handle_message(conn, msg)

    def on_disconnect(conn):
        rooms.disconnect(conn)
        users.disconnect(conn)

    server = TCPServer("127.0.0.1", 0, client_timeout=10.0,
                       on_message=on_message, on_disconnect=on_disconnect)
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

        wait_for(tk_root, lambda:
            first_app.lobby_view is not None
            and second_app.lobby_view is not None
            and first_app.connection_var.get() == "CONNECTED"
            and second_app.connection_var.get() == "CONNECTED",
        )

        first_app._create_room()
        wait_for(tk_root, lambda: first_app.room_view is not None)
        room_id = first_app.active_room_id

        second_app._refresh_rooms()
        wait_for(tk_root, lambda:
            second_app.lobby_view is not None
            and room_id in second_app.lobby_view._rooms,
        )
        second_app._join_room(room_id)
        wait_for(tk_root, lambda:
            first_app.room_view is not None
            and second_app.room_view is not None
            and first_app.room_view.status_var.get() == "READY",
        )

        first_app._start_match(room_id)
        wait_for(tk_root, lambda:
            first_app.room_view is not None
            and second_app.room_view is not None
            and first_app.room_view.status_var.get() == "IN PROGRESS",
        )

        fb = first_app.room_view.game_view
        sb = second_app.room_view.game_view
        wait_for(tk_root, lambda: sb._current_player == "X")

        # Alice (X) plays 5 in a row at row 7 cols 0-4 (Bob plays top row to avoid winning)
        half = GameView.CELL_SIZE / 2
        margin = GameView.MARGIN
        cs = GameView.CELL_SIZE

        # Turn 1: Alice plays (7,0), Bob plays (0,0)
        fb._handle_board_click(SimpleNamespace(x=margin + 0 * cs + half, y=margin + 7 * cs + half))
        wait_for(tk_root, lambda:
            fb._board is not None and fb._board[7][0] == "X"
            and sb._current_player == "O"
        )
        sb._handle_board_click(SimpleNamespace(x=margin + 0 * cs + half, y=margin + 0 * cs + half))
        wait_for(tk_root, lambda:
            sb._board is not None and sb._board[0][0] == "O"
            and fb._current_player == "X"
        )

        # Turn 2: Alice (7,1), Bob (0,1)
        fb._handle_board_click(SimpleNamespace(x=margin + 1 * cs + half, y=margin + 7 * cs + half))
        wait_for(tk_root, lambda: fb._board[7][1] == "X" and sb._current_player == "O")
        sb._handle_board_click(SimpleNamespace(x=margin + 1 * cs + half, y=margin + 0 * cs + half))
        wait_for(tk_root, lambda: sb._board[0][1] == "O" and fb._current_player == "X")

        # Turn 3: Alice (7,2), Bob (0,2)
        fb._handle_board_click(SimpleNamespace(x=margin + 2 * cs + half, y=margin + 7 * cs + half))
        wait_for(tk_root, lambda: fb._board[7][2] == "X" and sb._current_player == "O")
        sb._handle_board_click(SimpleNamespace(x=margin + 2 * cs + half, y=margin + 0 * cs + half))
        wait_for(tk_root, lambda: sb._board[0][2] == "O" and fb._current_player == "X")

        # Turn 4: Alice (7,3), Bob (0,3)
        fb._handle_board_click(SimpleNamespace(x=margin + 3 * cs + half, y=margin + 7 * cs + half))
        wait_for(tk_root, lambda: fb._board[7][3] == "X" and sb._current_player == "O")
        sb._handle_board_click(SimpleNamespace(x=margin + 3 * cs + half, y=margin + 0 * cs + half))
        wait_for(tk_root, lambda: sb._board[0][3] == "O" and fb._current_player == "X")

        # Turn 5: Alice wins! (7,4)
        fb._handle_board_click(SimpleNamespace(x=margin + 4 * cs + half, y=margin + 7 * cs + half))
        wait_for(tk_root, lambda:
            fb._game_state in ("X_WON", "O_WON", "DRAW")
            and sb._game_state in ("X_WON", "O_WON", "DRAW"),
        )

        # Both views see the same game state
        assert fb._game_state == "X_WON"
        assert sb._game_state == "X_WON"

        # Board is identical on both clients
        assert fb._board == sb._board

        # Winner info available on both views (via ROOM_UPDATE or GAME_END)
        # At least one client must have overlay set
        assert fb._overlay_shown_for == "X_WON" or sb._overlay_shown_for == "X_WON"

        # Result text
        assert "CHIẾN THẮNG" in fb.result_var.get()
        assert "BẠN ĐÃ THUA" in sb.result_var.get()

        # Board locked: further clicks ignored
        extra_moves_before = []
        orig_on_move_fb = fb._on_move
        fb._on_move = lambda r, row, col: extra_moves_before.append((r, row, col))
        fb._handle_board_click(SimpleNamespace(x=margin + 5 * cs + half, y=margin + 7 * cs + half))
        fb._on_move = orig_on_move_fb
        assert extra_moves_before == [], "Board must be locked after game ends"

    finally:
        first_app.stop()
        second_app.stop()
        second_window.destroy()
        server.stop()


def test_server_handles_multiple_rooms_independently(tk_root: tk.Tk) -> None:
    """Server remains operational for other rooms after one room's match ends."""
    from tests.test_room_manager import setup_players  # type: ignore

    _, rooms, (alice, bob, carol, dave) = setup_players(
        "Alice", "Bob", "Carol", "Dave"
    )

    room1 = rooms.create_room(alice)
    rooms.join_room(bob, room1.room_id)
    rooms.start_match(alice, room1.room_id)
    room2 = rooms.create_room(carol)
    rooms.join_room(dave, room2.room_id)
    rooms.start_match(carol, room2.room_id)

    for col in range(5):
        rooms.make_move(alice, room1.room_id, 7, col)
        if col < 4:
            rooms.make_move(bob, room1.room_id, 0, col)

    ended = rooms.get_room(room1.room_id)
    assert ended is not None and ended.status is RoomStatus.ENDED

    room2_snap = rooms.get_room(room2.room_id)
    assert room2_snap is not None and room2_snap.status is RoomStatus.IN_PROGRESS

    moved = rooms.make_move(carol, room2.room_id, 1, 1)
    assert moved.status is RoomStatus.IN_PROGRESS
    assert moved.current_player is Piece.O
    assert moved.history[-1]["player"] == "Carol"
