"""Shared models and constants for Caro game."""

from dataclasses import dataclass
from enum import Enum


@dataclass
class Move:
    """Represents a move on the Caro board."""

    row: int
    col: int
    player: str



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
