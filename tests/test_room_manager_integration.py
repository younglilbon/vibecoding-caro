"""TCP integration test for UserManager and RoomManager message routing."""

import socket
from weakref import WeakKeyDictionary

from Code.Server.game_engine import Piece
from Code.Server.room_manager import RoomManager
from Code.Server.server import TCPServer
from Code.Server.user_manager import UserManager
from Code.Shared.message_types import MessageType
from Code.Shared.protocol import MessageProtocol

_RECEIVE_BUFFERS: WeakKeyDictionary[socket.socket, bytearray] = WeakKeyDictionary()


def receive(client: socket.socket) -> dict[str, object]:
    buffer = _RECEIVE_BUFFERS.setdefault(client, bytearray())
    message, buffer = MessageProtocol.receive_message(client, buffer)
    _RECEIVE_BUFFERS[client] = buffer
    assert message is not None
    return message


def receive_room_status(client: socket.socket, status: str) -> dict[str, object]:
    while True:
        message = receive(client)
        if message.get("type") == MessageType.ROOM_UPDATE.value and message.get("status") == status:
            return message


def test_user_login_room_create_join_start_and_disconnect() -> None:
    users = UserManager()
    rooms = RoomManager(users)

    def handle_message(connection, message):
        if not users.handle_message(connection, message):
            rooms.handle_message(connection, message)

    def handle_disconnect(connection):
        player = users.get_player(connection)
        users.disconnect(connection)
        rooms.disconnect(connection, player_name=player.name if player is not None else None)

    server = TCPServer(
        "127.0.0.1",
        0,
        client_timeout=10.0,
        on_message=handle_message,
        on_disconnect=handle_disconnect,
    )
    address = server.start()
    alice = socket.create_connection(address, timeout=10.0)
    bob = socket.create_connection(address, timeout=10.0)
    try:
        MessageProtocol.send_message(alice, {"type": MessageType.JOIN.value, "name": "Alice"})
        assert receive(alice)["type"] == MessageType.PLAYER_LIST.value
        MessageProtocol.send_message(bob, {"type": MessageType.JOIN.value, "name": "Bob"})
        receive(alice)
        bob_login = receive(bob)
        bob_token = bob_login["resume_token"]

        MessageProtocol.send_message(alice, {"type": MessageType.CREATE_ROOM.value})
        created = receive(alice)
        assert created["status"] == "WAITING"
        room_id = created["room_id"]

        MessageProtocol.send_message(bob, {"type": MessageType.ROOM_LIST.value})
        room_list = receive(bob)
        if room_list["type"] == MessageType.ROOM_LIST_UPDATE.value:
            room_list = receive(bob)
        assert room_list["type"] == MessageType.ROOM_LIST.value
        assert room_list["rooms"][0]["room_id"] == room_id

        MessageProtocol.send_message(
            bob,
            {"type": MessageType.JOIN_ROOM.value, "room_id": room_id},
        )
        assert receive_room_status(alice, "READY")["status"] == "READY"
        assert receive_room_status(bob, "READY")["status"] == "READY"

        MessageProtocol.send_message(
            alice,
            {"type": MessageType.GAME_START.value, "room_id": room_id},
        )
        assert receive_room_status(alice, "IN_PROGRESS")["status"] == "IN_PROGRESS"
        assert receive_room_status(bob, "IN_PROGRESS")["status"] == "IN_PROGRESS"

        bob.close()
        bob = None
        paused = receive_room_status(alice, "PAUSED")
        assert paused["event"] == "PLAYER_DISCONNECTED"
        assert paused["disconnected_player"] == "Bob"
        assert paused["authority"] == "SERVER"
        assert rooms.get_room(room_id).status.value == "PAUSED"

        bob = socket.create_connection(address, timeout=10.0)
        MessageProtocol.send_message(
            bob,
            {
                "type": MessageType.JOIN.value,
                "name": "Bob",
                "resume_token": bob_token,
            },
        )
        resumed = receive_room_status(bob, "IN_PROGRESS")
        assert resumed["event"] == "PLAYER_RECONNECTED"
        assert resumed["current_player"] == "X"
        receive_room_status(alice, "IN_PROGRESS")
        assert rooms.get_room(room_id).status.value == "IN_PROGRESS"

        MessageProtocol.send_message(
            alice,
            {
                "type": MessageType.MOVE.value,
                "room_id": room_id,
                "row": 6,
                "col": 8,
            },
        )
        alice_move = receive_room_status(alice, "IN_PROGRESS")
        bob_move = receive_room_status(bob, "IN_PROGRESS")
        assert alice_move["history"] == bob_move["history"]
        assert alice_move["history"][0]["row"] == 6
        assert alice_move["board"] == bob_move["board"]

        MessageProtocol.send_message(
            alice,
            {"type": MessageType.SURRENDER.value, "room_id": room_id},
        )
        alice_ended = receive_room_status(alice, "ENDED")
        bob_ended = receive_room_status(bob, "ENDED")
        assert alice_ended["winner"] == bob_ended["winner"] == "Bob"
        assert alice_ended["reason"].endswith("SURRENDER).")

        MessageProtocol.send_message(
            alice,
            {
                "type": MessageType.PLAY_AGAIN.value,
                "room_id": room_id,
                "accepted": True,
            },
        )
        receive_room_status(alice, "ENDED")
        receive_room_status(bob, "ENDED")
        MessageProtocol.send_message(
            bob,
            {
                "type": MessageType.PLAY_AGAIN.value,
                "room_id": room_id,
                "accepted": True,
            },
        )
        alice_replay = receive_room_status(alice, "IN_PROGRESS")
        bob_replay = receive_room_status(bob, "IN_PROGRESS")
        assert alice_replay["event"] == bob_replay["event"] == "REPLAY_STARTED"
        assert alice_replay["board"] == bob_replay["board"]
        assert all(piece == " " for row in alice_replay["board"] for piece in row)
        assert alice_replay["history"] == bob_replay["history"]
        assert len(alice_replay["history"]) == 1
    finally:
        alice.close()
        if bob is not None:
            bob.close()
        server.stop()
        rooms.close()
        users.close()


def test_moves_are_authenticated_validated_and_broadcast_over_tcp() -> None:
    users = UserManager()
    rooms = RoomManager(users)

    def handle_message(connection, message):
        if not users.handle_message(connection, message):
            rooms.handle_message(connection, message)

    def handle_disconnect(connection):
        player = users.get_player(connection)
        users.disconnect(connection)
        rooms.disconnect(connection, player_name=player.name if player is not None else None)

    server = TCPServer(
        "127.0.0.1",
        0,
        client_timeout=10.0,
        on_message=handle_message,
        on_disconnect=handle_disconnect,
    )
    address = server.start()
    alice = socket.create_connection(address, timeout=10.0)
    bob = socket.create_connection(address, timeout=10.0)
    try:
        MessageProtocol.send_message(alice, {"type": MessageType.JOIN.value, "name": "Alice"})
        receive(alice)
        MessageProtocol.send_message(bob, {"type": MessageType.JOIN.value, "name": "Bob"})
        receive(alice)
        receive(bob)

        MessageProtocol.send_message(alice, {"type": MessageType.CREATE_ROOM.value})
        room_id = receive(alice)["room_id"]
        MessageProtocol.send_message(
            bob,
            {"type": MessageType.JOIN_ROOM.value, "room_id": room_id},
        )
        receive_room_status(alice, "READY")
        receive_room_status(bob, "READY")
        MessageProtocol.send_message(
            alice,
            {"type": MessageType.GAME_START.value, "room_id": room_id},
        )
        receive_room_status(alice, "IN_PROGRESS")
        receive_room_status(bob, "IN_PROGRESS")
        state_before_client_update = rooms.get_room(room_id)
        version_before_client_update = rooms.synchronization.get_version(room_id)
        MessageProtocol.send_message(
            alice,
            {
                "type": MessageType.ROOM_UPDATE.value,
                "room_id": room_id,
                "version": 999999,
                "authority": "CLIENT",
                "game_state": "X_WON",
                "board": [["X"]],
            },
        )
        assert rooms.get_room(room_id) == state_before_client_update
        assert rooms.synchronization.get_version(room_id) == version_before_client_update

        MessageProtocol.send_message(
            alice,
            {
                "type": MessageType.MOVE.value,
                "room_id": room_id,
                "row": 4,
                "col": 5,
                "username": "Bob",
                "player": "O",
            },
        )
        alice_update = receive(alice)
        bob_update = receive(bob)
        assert alice_update == bob_update
        assert alice_update["board"][4][5] == Piece.X.value
        assert alice_update["current_player"] == Piece.O.value
        assert alice_update["authority"] == "SERVER"
        assert alice_update["version"] == version_before_client_update + 1
        state_before_invalid_moves = rooms.get_room(room_id)
        version_before_invalid_moves = rooms.synchronization.get_version(room_id)

        invalid_moves = [
            (alice, {"row": 4, "col": 6}),
            (bob, {"row": 4, "col": 5}),
            (bob, {"row": 15, "col": 0}),
        ]
        for client, coordinates in invalid_moves:
            MessageProtocol.send_message(
                client,
                {"type": MessageType.MOVE.value, "room_id": room_id, **coordinates},
            )
            assert receive(client)["type"] == MessageType.ERROR.value
            assert rooms.get_room(room_id) == state_before_invalid_moves
            assert rooms.synchronization.get_version(room_id) == version_before_invalid_moves

        latest_version = version_before_invalid_moves

        def play_move(client: socket.socket, row: int, col: int) -> tuple[dict[str, object], dict[str, object]]:
            nonlocal latest_version
            MessageProtocol.send_message(
                client,
                {"type": MessageType.MOVE.value, "room_id": room_id, "row": row, "col": col},
            )
            alice_message, bob_message = receive(alice), receive(bob)
            assert alice_message["version"] == bob_message["version"] == latest_version + 1
            latest_version = alice_message["version"]
            return alice_message, bob_message

        first_o_move = play_move(bob, 0, 0)
        assert first_o_move[0] == first_o_move[1]
        assert first_o_move[0]["board"][0][0] == Piece.O.value

        for col in range(5):
            x_update = play_move(alice, 7, col)
            assert x_update[0] == x_update[1]
            if col < 4:
                o_update = play_move(bob, 0, 10 + col)
                assert o_update[0] == o_update[1]

        final_update = rooms.get_room(room_id)
        assert final_update.status.value == "ENDED"
        assert final_update.game_state.value == "X_WON"
    finally:
        alice.close()
        bob.close()
        server.stop()