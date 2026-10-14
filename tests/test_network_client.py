"""Tests for the asynchronous client transport and shared protocol use."""

import queue
import socket
import threading

from Code.Client.network_client import NetworkClient, NetworkEvent
from Code.Shared.message_types import MessageType
from Code.Shared.protocol import MessageProtocol


def next_event(events: queue.Queue[NetworkEvent], kind: str, timeout: float = 2.0) -> NetworkEvent:
    deadline = threading.Event()
    end_time = __import__("time").monotonic() + timeout
    while not deadline.is_set():
        remaining = end_time - __import__("time").monotonic()
        if remaining <= 0:
            raise AssertionError(f"Timed out waiting for {kind} event")
        event = events.get(timeout=remaining)
        if event.kind == kind:
            return event
    raise AssertionError(f"Timed out waiting for {kind} event")


def test_worker_connects_sends_and_receives_with_shared_protocol() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    address = listener.getsockname()
    received: queue.Queue[dict[str, object]] = queue.Queue()

    def serve_once() -> None:
        connection, _ = listener.accept()
        connection.settimeout(2.0)
        try:
            message, _ = MessageProtocol.receive_message(connection, bytearray())
            received.put(message)
            MessageProtocol.send_message(
                connection,
                {"type": MessageType.ROOM_LIST.value, "rooms": []},
            )
        finally:
            connection.close()
            listener.close()

    server_thread = threading.Thread(target=serve_once, daemon=True)
    server_thread.start()
    events: queue.Queue[NetworkEvent] = queue.Queue()
    client = NetworkClient(events, poll_timeout=0.05)
    try:
        client.connect(address[0], address[1])
        assert next_event(events, "status").payload["state"] == "CONNECTING"
        assert next_event(events, "status").payload["state"] == "CONNECTED"
        assert client.send({"type": MessageType.ROOM_LIST.value}) is True
        assert received.get(timeout=2.0) == {"type": MessageType.ROOM_LIST.value}
        message_event = next_event(events, "message")
        assert message_event.payload["message"] == {
            "type": MessageType.ROOM_LIST.value,
            "rooms": [],
        }
    finally:
        client.close()
        server_thread.join(timeout=2.0)


def test_failed_connection_reports_disconnected_status() -> None:
    events: queue.Queue[NetworkEvent] = queue.Queue()
    client = NetworkClient(events, connect_timeout=0.5)

    client.connect("127.0.0.1", 1)

    assert next_event(events, "status").payload["state"] == "CONNECTING"
    assert next_event(events, "error").payload["message"]
    disconnected = next_event(events, "status")
    assert disconnected.payload["state"] == "DISCONNECTED"