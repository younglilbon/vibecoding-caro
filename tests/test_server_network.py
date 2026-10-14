"""TCP server integration tests with concurrent test clients."""

import queue
import socket
import struct
from contextlib import ExitStack

import pytest

from Code.Server.server import TCPServer
from Code.Shared.message_types import MessageType
from Code.Shared.protocol import MessageProtocol


@pytest.fixture
def running_server():
    messages: queue.Queue[dict[str, object]] = queue.Queue()
    server = TCPServer(
        "127.0.0.1",
        0,
        client_timeout=2.0,
        on_message=lambda _handler, message: messages.put(message),
    )
    address = server.start()
    try:
        yield server, address, messages
    finally:
        server.stop()


def test_accepts_and_processes_two_clients_concurrently(running_server) -> None:
    _, address, messages = running_server
    with ExitStack() as clients:
        first = clients.enter_context(socket.create_connection(address, timeout=2.0))
        second = clients.enter_context(socket.create_connection(address, timeout=2.0))
        MessageProtocol.send_message(first, {"type": MessageType.HEARTBEAT.value, "client": "one"})
        MessageProtocol.send_message(second, {"type": MessageType.HEARTBEAT.value, "client": "two"})

        received = {messages.get(timeout=2.0)["client"] for _ in range(2)}

    assert received == {"one", "two"}


def test_invalid_client_does_not_stop_server(running_server) -> None:
    server, address, messages = running_server
    invalid_payload = b"{invalid-json"
    with socket.create_connection(address, timeout=2.0) as invalid_client:
        invalid_client.sendall(struct.pack(">I", len(invalid_payload)) + invalid_payload)
        response, _ = MessageProtocol.receive_message(invalid_client, bytearray())
        assert response == {"type": MessageType.ERROR.value, "message": "Invalid message"}
        assert invalid_client.recv(1) == b""

    with socket.create_connection(address, timeout=2.0) as healthy_client:
        MessageProtocol.send_message(
            healthy_client,
            {"type": MessageType.HEARTBEAT.value, "client": "healthy"},
        )

    assert messages.get(timeout=2.0)["client"] == "healthy"
    assert server.is_running


def test_idle_client_times_out_without_stopping_server() -> None:
    server = TCPServer("127.0.0.1", 0, client_timeout=0.1)
    address = server.start()
    try:
        with socket.create_connection(address, timeout=2.0) as client:
            assert client.recv(1) == b""
        assert server.is_running
    finally:
        server.stop()


def test_stop_closes_active_client_connection(running_server) -> None:
    server, address, messages = running_server
    client = socket.create_connection(address, timeout=2.0)
    MessageProtocol.send_message(
        client,
        {"type": MessageType.HEARTBEAT.value, "client": "active"},
    )
    assert messages.get(timeout=2.0)["client"] == "active"
    server.stop()
    try:
        assert client.recv(1) == b""
        assert not server.is_running
    finally:
        client.close()