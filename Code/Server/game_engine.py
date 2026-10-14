"""Core game engine for Game Caro Online.

This module contains the GameEngine class that implements the core game logic
for a 15x15 Caro board with X/O pieces.
"""

from enum import Enum
from typing import List, Optional, Tuple


BOARD_SIZE = 15
WIN_STREAK = 5


class Piece(str, Enum):
    """Represents a piece on the Caro board."""

    EMPTY = " "
    X = "X"
    O = "O"

    @property
    def opponent(self) -> "Piece":
        """Get the opponent piece."""
        if self == Piece.X:
            return Piece.O
        if self == Piece.O:
            return Piece.X
        raise ValueError("EMPTY has no opponent")


class GameState(str, Enum):
    """Represents the current state of a Caro match."""

    IN_PROGRESS = "IN_PROGRESS"
    X_WON = "X_WON"
    O_WON = "O_WON"
    DRAW = "DRAW"


class Position:
    """Represents a position on the 15x15 board."""

    def __init__(self, row: int, col: int) -> None:
        self.row = row
        self.col = col

    def __str__(self) -> str:
        return f"({self.row},{self.col})"

    @staticmethod
    def from_coords(row: int, col: int) -> "Position":
        """Create a Position from row and column coordinates."""
        return Position(row, col)


class GameEngine:
    """Main game engine for Caro game."""

    def __init__(self) -> None:
        self.board: List[List[Piece]] = []
        self.current_player: Piece = Piece.X
        self.state: GameState = GameState.IN_PROGRESS
        self.move_count: int = 0
        self.winning_line: List[Tuple[int, int]] = []
        self._initialize_board()

    def _initialize_board(self) -> None:
        """Create a fresh 15x15 board filled with EMPTY pieces."""
        self.board = [[Piece.EMPTY for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.move_count = 0
        self.winning_line = []

    def create_new_game(self) -> None:
        """Create a new game with fresh board state."""
        self._initialize_board()
        self.current_player = Piece.X
        self.state = GameState.IN_PROGRESS
        self.winning_line = []

    def get_winning_line(self) -> List[Tuple[int, int]]:
        """Return the coordinates of the winning line."""
        return list(self.winning_line)

    def get_board(self) -> List[List[Piece]]:
        """Return a copy of the current board state."""
        return [row[:] for row in self.board]

    @staticmethod
    def is_valid_coordinate(row: int, col: int) -> bool:
        """Return whether integer coordinates fall within the board."""
        return (
            type(row) is int
            and type(col) is int
            and 0 <= row < BOARD_SIZE
            and 0 <= col < BOARD_SIZE
        )

    def is_valid_move(self, position: Position, player: Optional[Piece] = None) -> bool:
        """Check if a move is valid.

        Args:
            position: The target position on the board
            player: Optional player making the move; defaults to the current player

        Returns:
            True if the move is valid, False otherwise
        """
        # Check if position is within bounds
        if not self.is_valid_coordinate(position.row, position.col):
            return False

        # Check if position is empty
        if self.board[position.row][position.col] != Piece.EMPTY:
            return False

        # Check if game is still in progress
        if self.state != GameState.IN_PROGRESS:
            return False

        return player is None or player == self.current_player

    def make_move(
        self, position: Position, player: Optional[Piece] = None
    ) -> Tuple[bool, str]:
        """Make a move on the board.

        Args:
            position: The target position for the move
            player: Optional player making the move

        Returns:
            Tuple of (success, message) where success is True if move was successful
        """
        if not self.is_valid_move(position, player):
            return False, "Invalid move"

        # Place the piece
        self.board[position.row][position.col] = self.current_player
        self.move_count += 1

        # Check for win condition
        if self._check_winner(position):
            self.state = GameState.X_WON if self.current_player == Piece.X else GameState.O_WON
            return True, f"Player {self.current_player.value} wins!"

        # Check for draw condition
        if self.move_count >= BOARD_SIZE * BOARD_SIZE:
            self.state = GameState.DRAW
            return True, "Game is a draw!"

        # Switch player
        self.current_player = self.current_player.opponent
        return True, "Move successful"

    def _check_winner(self, position: Position) -> bool:
        """Check if the current player has won by having 5 in a row.

        Args:
            position: The position where the last move was made

        Returns:
            True if the current player has won, False otherwise
        """
        piece = self.board[position.row][position.col]
        if piece == Piece.EMPTY:
            return False

        # Check horizontal
        if self._check_line(piece, position.row, position.col, 0, 1):
            return True

        # Check vertical
        if self._check_line(piece, position.row, position.col, 1, 0):
            return True

        # Check diagonal (top-left to bottom-right)
        if self._check_line(piece, position.row, position.col, 1, 1):
            return True

        # Check diagonal (top-right to bottom-left)
        if self._check_line(piece, position.row, position.col, 1, -1):
            return True

        return False

    def _check_line(self, piece: Piece, row: int, col: int, d_row: int, d_col: int) -> bool:
        """Check for at least five contiguous pieces through a position.

        Args:
            piece: The piece to check
            row: Starting row
            col: Starting column
            d_row: Row direction
            d_col: Column direction

        Returns:
            True if 5 in a row are found, False otherwise
        """
        dist_neg = 0
        while True:
            r = row + (-1) * (dist_neg + 1) * d_row
            c = col + (-1) * (dist_neg + 1) * d_col
            if not (0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE):
                break
            if self.board[r][c] != piece:
                break
            dist_neg += 1

        dist_pos = 0
        while True:
            r = row + 1 * (dist_pos + 1) * d_row
            c = col + 1 * (dist_pos + 1) * d_col
            if not (0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE):
                break
            if self.board[r][c] != piece:
                break
            dist_pos += 1

        count = 1 + dist_neg + dist_pos
        if count >= WIN_STREAK:
            full_line = [
                (row + step * d_row, col + step * d_col)
                for step in range(-dist_neg, dist_pos + 1)
            ]
            self.winning_line = full_line[:WIN_STREAK] if len(full_line) >= WIN_STREAK else full_line
            return True
        return False

    def is_draw(self) -> bool:
        """Check if the game is a draw (board is full with no winner)."""
        return self.state == GameState.DRAW

    def get_game_state(self) -> GameState:
        """Return the current game state."""
        return self.state

    def get_current_player(self) -> Piece:
        """Return the current player."""
        return self.current_player

    def get_board_size(self) -> int:
        """Return the size of the board."""
        return BOARD_SIZE

