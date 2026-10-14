"""Command-line entry point for the threaded Caro TCP server."""

import argparse
import logging
import threading
from collections.abc import Sequence

from Code.Server.match_history_store import MatchHistoryStore
from Code.Server.server import TCPServer
from Code.Server.room_manager import RoomManager
from Code.Server.user_manager import UserManager
from Code.Shared.constants import DEFAULT_HOST, DEFAULT_PORT

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> None:
    """Parse server options, start listening, and stop cleanly on Ctrl+C."""
    parser = argparse.ArgumentParser(description="Run the Caro TCP server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="interface address to bind")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port to bind")
    parser.add_argument(
        "--client-timeout",
        type=float,
        default=60.0,
        help="idle timeout in seconds for each client",
    )
    parser.add_argument(
        "--database",
        default="caro_history.sqlite3",
        help="SQLite file used to persist completed match histories",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.client_timeout <= 0:
        parser.error("--client-timeout must be greater than zero")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    users = UserManager()
    history_store = MatchHistoryStore(args.database)
    rooms = RoomManager(users, history_store=history_store)

    def handle_message(connection, message):
        if message.get("type") == "LEAVE":
            player = users.get_player(connection)
            if player is None:
                users.handle_message(connection, message)
                return
            rooms.leave_spectator(connection)
            rooms.leave_room(connection)
            users.logout(connection)
            connection.stop()
            return
        if not users.handle_message(connection, message):
            rooms.handle_message(connection, message)

    def handle_disconnect(connection):
        player = users.get_player(connection)
        users.disconnect(connection)
        rooms.disconnect(connection, player_name=player.name if player is not None else None)

    server = TCPServer(
        args.host,
        args.port,
        client_timeout=args.client_timeout,
        on_message=handle_message,
        on_disconnect=handle_disconnect,
    )
    address = server.start()
    logger.info("Server listening on %s:%s", address[0], address[1])
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    finally:
        server.stop()
        rooms.close()
        users.close()


if __name__ == "__main__":
    main()