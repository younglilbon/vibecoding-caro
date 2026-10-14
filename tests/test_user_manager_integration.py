"""Integration tests for lobby flows over the threaded TCP server."""

import socket

from Code.Server.server import TCPServer
from Code.Server.user_manager import UserManager
from Code.Shared.message_types import MessageType
from Code.Shared.protocol import MessageProtocol


def receive(client: socket.socket) -> dict[str, object]:
    message, _ = MessageProtocol.receive_message(client, bytearray())
    assert message is not None
    return message


def test_login_invite_accept_and_disconnect_update_over_tcp() -> None:
    users = UserManager()
    server = TCPServer(
        "127.0.0.1",
        0,
        client_timeout=3.0,
        on_message=users.handle_message,
        on_disconnect=users.disconnect,
    )
    address = server.start()
    alice = socket.create_connection(address, timeout=3.0)
    bob = socket.create_connection(address, timeout=3.0)
    try:
        MessageProtocol.send_message(alice, {"type": MessageType.JOIN.value, "name": "Alice"})
        alice_login = receive(alice)
        assert alice_login["type"] == MessageType.PLAYER_LIST.value
        assert alice_login["players"] == ["Alice"]
        assert isinstance(alice_login["resume_token"], str)

        MessageProtocol.send_message(bob, {"type": MessageType.JOIN.value, "name": "Bob"})
        alice_list = receive(alice)
        bob_login = receive(bob)
        assert alice_list["type"] == bob_login["type"] == MessageType.PLAYER_LIST.value
        assert alice_list["players"] == bob_login["players"] == ["Alice", "Bob"]
        assert isinstance(bob_login["resume_token"], str)

        MessageProtocol.send_message(
            alice,
            {"type": MessageType.INVITE.value, "target": "Bob", "from": "Mallory"},
        )
        incoming_invite = receive(bob)
        assert incoming_invite["type"] == MessageType.INVITE.value
        assert incoming_invite["from"] == "Alice"
        invite_id = incoming_invite["invite_id"]
        pending = receive(alice)
        assert pending["status"] == "PENDING"

        MessageProtocol.send_message(
            bob,
            {"type": MessageType.INVITE_ACCEPT.value, "invite_id": invite_id},
        )
        assert receive(alice)["status"] == "ACCEPTED"
        assert receive(bob)["status"] == "ACCEPTED"

        bob.close()
        bob = None
        alice_update = receive(alice)
        assert alice_update["type"] == MessageType.PLAYER_LIST.value
        assert alice_update["players"] == ["Alice"]
        assert users.get_online_players() == ["Alice"]
    finally:
        alice.close()
        if bob is not None:
            bob.close()
        server.stop()