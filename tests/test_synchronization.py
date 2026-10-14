"""Tests for versioned and permission-scoped room state synchronization."""

import threading

from Code.Server.synchronization import RoomAudience, Synchronization
from Code.Shared.message_types import MessageType


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def send_message(self, message: dict[str, object]) -> None:
        self.messages.append(message)


def room_state(status: str = "IN_PROGRESS", current_player: str = "X") -> dict[str, object]:
    return {
        "type": MessageType.ROOM_UPDATE.value,
        "status": status,
        "current_player": current_player,
        "game_state": "IN_PROGRESS",
        "board": [[" ", "X"]],
    }


def test_broadcast_reaches_only_room_players_and_viewers() -> None:
    synchronization = Synchronization()
    alice, bob, viewer, outsider = (FakeConnection() for _ in range(4))
    audience = RoomAudience(players=(alice, bob), viewers=(viewer,))

    version = synchronization.publish_state("room-1", room_state(), audience)

    assert version == 1
    for connection in (alice, bob, viewer):
        assert connection.messages[0]["version"] == 1
        assert connection.messages[0]["authority"] == "SERVER"
    assert outsider.messages == []
    assert synchronization.send_current_state("room-1", outsider, audience) is False
    assert outsider.messages == []


def test_versions_increase_and_reconnect_receives_latest_cached_state() -> None:
    synchronization = Synchronization()
    alice, bob = FakeConnection(), FakeConnection()
    audience = RoomAudience(players=(alice, bob))

    assert synchronization.publish_state("room-1", room_state(), audience) == 1
    newer_state = room_state(current_player="O")
    newer_state["board"] = [["X", "O"]]
    assert synchronization.publish_state("room-1", newer_state, audience) == 2
    assert synchronization.get_version("room-1") == 2

    reconnected = FakeConnection()
    reconnect_audience = RoomAudience(players=(reconnected,))
    assert synchronization.send_current_state("room-1", reconnected, reconnect_audience) is True
    assert reconnected.messages == [synchronization.get_current_state("room-1")]
    assert reconnected.messages[0]["version"] == 2
    assert reconnected.messages[0]["current_player"] == "O"


def test_disconnect_is_a_versioned_room_state_event() -> None:
    synchronization = Synchronization()
    alice = FakeConnection()

    version = synchronization.notify_player_disconnected(
        "room-1",
        room_state(status="ENDED"),
        RoomAudience(players=(alice,)),
        "Bob",
    )

    assert version == 1
    assert alice.messages[0]["event"] == "PLAYER_DISCONNECTED"
    assert alice.messages[0]["disconnected_player"] == "Bob"
    assert alice.messages[0]["status"] == "ENDED"


def test_concurrent_broadcasts_preserve_version_order() -> None:
    synchronization = Synchronization()
    entered_first_send = threading.Event()
    release_first_send = threading.Event()
    second_send_done = threading.Event()

    class BlockingConnection(FakeConnection):
        def send_message(self, message: dict[str, object]) -> None:
            if message["version"] == 1:
                entered_first_send.set()
                assert release_first_send.wait(timeout=2.0)
            super().send_message(message)
            if message["version"] == 2:
                second_send_done.set()

    client = BlockingConnection()
    audience = RoomAudience(players=(client,))
    first = threading.Thread(target=synchronization.publish_state, args=("room-1", room_state(), audience))
    second_state = room_state(current_player="O")
    second = threading.Thread(target=synchronization.publish_state, args=("room-1", second_state, audience))
    first.start()
    try:
        assert entered_first_send.wait(timeout=1.0)
        second.start()
        assert not second_send_done.wait(timeout=0.2)
    finally:
        release_first_send.set()
        first.join(timeout=2.0)
        if second.ident is not None:
            second.join(timeout=2.0)

    assert [message["version"] for message in client.messages] == [1, 2]