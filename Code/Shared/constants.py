"""Game constants shared between client and server."""

# Board size
BOARD_SIZE = 15

# Winning condition
WIN_LENGTH = 5

# Player symbols
PLAYER_X = 'X'
PLAYER_O = 'O'
EMPTY = ' '

# Network defaults
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 12345

# Protocol Limits
MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB
HEADER_SIZE = 4                 # 4 bytes for network prefix
