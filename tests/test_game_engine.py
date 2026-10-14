"""Boundary-focused tests for the standalone Caro game engine."""

import pytest

from Code.Server.game_engine import BOARD_SIZE, GameEngine, GameState, Piece, Position


def test_new_game_has_an_empty_15_by_15_board() -> None:
    game = GameEngine()

    board = game.get_board()
    assert len(board) == BOARD_SIZE == 15
    assert all(len(row) == BOARD_SIZE for row in board)
    assert all(piece is Piece.EMPTY for row in board for piece in row)
    assert game.get_current_player() is Piece.X
    assert game.get_game_state() is GameState.IN_PROGRESS


@pytest.mark.parametrize(
    ("row", "col", "expected"),
    [
        (0, 0, True),
        (14, 14, True),
        (-1, 0, False),
        (0, -1, False),
        (15, 0, False),
        (0, 15, False),
        (1.5, 0, False),
    ],
)
def test_coordinate_validation(row: int, col: int, expected: bool) -> None:
    assert GameEngine.is_valid_coordinate(row, col) is expected


def test_rejects_out_of_bounds_and_occupied_moves() -> None:
    game = GameEngine()

    assert game.make_move(Position(-1, 0))[0] is False
    assert game.make_move(Position(0, 0))[0] is True
    assert game.make_move(Position(0, 0))[0] is False


def test_rejects_move_from_player_whose_turn_it_is_not() -> None:
    game = GameEngine()

    assert game.make_move(Position(0, 0), Piece.O)[0] is False
    assert game.get_board()[0][0] is Piece.EMPTY
    assert game.make_move(Position(0, 0), Piece.X)[0] is True


def test_turn_alternates_after_successful_moves() -> None:
    game = GameEngine()

    assert game.make_move(Position(0, 0))[0] is True
    assert game.get_current_player() is Piece.O
    assert game.make_move(Position(0, 1))[0] is True
    assert game.get_current_player() is Piece.X


@pytest.mark.parametrize("player", [Piece.X, Piece.O])
@pytest.mark.parametrize("direction", [(0, 1), (1, 0), (1, 1), (1, -1)])
def test_five_contiguous_pieces_win_in_every_direction(
    direction: tuple[int, int], player: Piece
) -> None:
    game = GameEngine()
    center_row, center_col = 7, 7
    row_step, col_step = direction
    for distance in (-2, -1, 1, 2):
        game.board[center_row + distance * row_step][center_col + distance * col_step] = player
    game.current_player = player

    succeeded, _ = game.make_move(Position(center_row, center_col), player)

    assert succeeded is True
    expected_state = GameState.X_WON if player is Piece.X else GameState.O_WON
    assert game.get_game_state() is expected_state


def test_six_contiguous_pieces_also_win() -> None:
    game = GameEngine()
    for col in range(2, 7):
        game.board[7][col] = Piece.X

    succeeded, _ = game.make_move(Position(7, 7), Piece.X)

    assert succeeded is True
    assert game.get_game_state() is GameState.X_WON


def test_rejects_moves_after_a_win() -> None:
    game = GameEngine()
    for col in range(4):
        game.board[7][col] = Piece.X

    assert game.make_move(Position(7, 4), Piece.X)[0] is True
    assert game.make_move(Position(0, 0), Piece.O)[0] is False
    assert game.get_game_state() is GameState.X_WON


def test_separated_pieces_do_not_count_as_a_win() -> None:
    game = GameEngine()
    for col in (4, 6, 8, 10):
        game.board[7][col] = Piece.X

    succeeded, _ = game.make_move(Position(7, 7), Piece.X)

    assert succeeded is True
    assert game.get_game_state() is GameState.IN_PROGRESS
    assert game.get_current_player() is Piece.O


def test_full_board_without_winner_is_a_draw() -> None:
    game = GameEngine()
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            game.board[row][col] = Piece.X if (row + 2 * col) % 5 < 2 else Piece.O
    game.board[14][14] = Piece.EMPTY
    game.move_count = BOARD_SIZE * BOARD_SIZE - 1
    game.current_player = Piece.O

    succeeded, _ = game.make_move(Position(14, 14), Piece.O)

    assert succeeded is True
    assert game.get_game_state() is GameState.DRAW
    assert game.is_draw() is True


def test_get_board_returns_a_copy() -> None:
    game = GameEngine()
    board_snapshot = game.get_board()
    board_snapshot[0][0] = Piece.X

    assert game.get_board()[0][0] is Piece.EMPTY


def test_new_game_resets_board_turn_and_state() -> None:
    game = GameEngine()
    game.make_move(Position(0, 0))
    game.create_new_game()

    assert all(piece is Piece.EMPTY for row in game.get_board() for piece in row)
    assert game.get_current_player() is Piece.X
    assert game.get_game_state() is GameState.IN_PROGRESS
    assert game.move_count == 0