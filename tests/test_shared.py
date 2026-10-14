"""Tests for shared module."""

from Code.Shared import constants, models


def test_constants():
    assert constants.BOARD_SIZE == 15
    assert constants.WIN_LENGTH == 5
    assert constants.EMPTY == models.Piece.EMPTY.value
    print("constants test passed")


def test_models():
    move = models.Move(row=0, col=0, player='X')
    assert move.row == 0
    print("models test passed")


if __name__ == "__main__":
    test_constants()
    test_models()