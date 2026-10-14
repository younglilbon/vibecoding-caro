"""Unit tests for authenticated identities and lobby invitations."""

import pytest

from Code.Server.user_manager import PlayerStatus, UserManager
from Code.Shared.message_types import MessageType


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.stopped = False

    def send_message(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    def stop(self) -> None:
        self.stopped = True


def send(manager: UserManager, connection: FakeConnection, message_type: MessageType, **fields: object) -> None:
    manager.handle_message(connection, {"type": message_type.value, **fields})  # type: ignore[arg-type]


def latest(connection: FakeConnection) -> dict[str, object]:
    return connection.messages[-1]


@pytest.mark.parametrize("name", ["", "ab", "bad name", "_alice", "a" * 21, "#alice", None, 123])
def test_rejects_invalid_names(name: object) -> None:
    manager = UserManager()
    connection = FakeConnection()

    send(manager, connection, MessageType.JOIN, name=name)

    assert manager.get_online_players() == []
    assert latest(connection)["type"] == MessageType.ERROR.value


@pytest.mark.parametrize("name", ["A_1", "a" * 20, "NgườiChơi"])
def test_accepts_valid_name_boundaries(name: str) -> None:
    assert UserManager.is_valid_name(name) is True


@pytest.mark.parametrize("name", ["Alice_", "Alice-"])
def test_rejects_names_with_trailing_separator(name: str) -> None:
    assert UserManager.is_valid_name(name) is False


def test_login_adds_player_and_duplicate_names_are_case_insensitive() -> None:
    manager = UserManager()
    first = FakeConnection()
    duplicate = FakeConnection()

    send(manager, first, MessageType.JOIN, name="Alice")
    send(manager, duplicate, MessageType.JOIN, name="alice")

    assert manager.get_online_players() == ["Alice"]
    assert latest(first)["type"] == MessageType.PLAYER_LIST.value
    assert latest(first)["players"] == ["Alice"]
    assert isinstance(latest(first)["resume_token"], str)
    assert len(latest(first)["resume_token"]) >= 40
    assert latest(duplicate)["type"] == MessageType.ERROR.value


def test_connection_cannot_change_its_identity_after_login() -> None:
    manager = UserManager()
    connection = FakeConnection()

    send(manager, connection, MessageType.JOIN, name="Alice")
    send(manager, connection, MessageType.JOIN, name="Other")

    assert manager.get_online_players() == ["Alice"]
    assert manager.get_player(connection).name == "Alice"
    assert latest(connection)["type"] == MessageType.ERROR.value


def test_invite_uses_socket_identity_and_only_target_can_accept() -> None:
    manager = UserManager()
    alice, bob, carol = FakeConnection(), FakeConnection(), FakeConnection()
    send(manager, alice, MessageType.JOIN, name="Alice")
    send(manager, bob, MessageType.JOIN, name="Bob")
    send(manager, carol, MessageType.JOIN, name="Carol")

    send(manager, alice, MessageType.INVITE, target="Bob", **{"from": "Carol"})
    invite = latest(bob)
    invite_id = invite["invite_id"]
    assert invite["from"] == "Alice"
    assert manager.get_player(alice).status is PlayerStatus.INVITE_PENDING
    assert manager.get_player(bob).status is PlayerStatus.INVITE_PENDING

    send(manager, carol, MessageType.INVITE_ACCEPT, invite_id=invite_id)
    assert latest(carol)["type"] == MessageType.ERROR.value
    assert manager.get_player(alice).status is PlayerStatus.INVITE_PENDING

    send(manager, bob, MessageType.INVITE_ACCEPT, invite_id=invite_id)
    assert latest(alice)["status"] == "ACCEPTED"
    assert latest(bob)["status"] == "ACCEPTED"
    assert manager.get_player(alice).status is PlayerStatus.ONLINE
    assert manager.get_player(bob).status is PlayerStatus.ONLINE


def test_rejects_self_offline_and_busy_invite_targets() -> None:
    manager = UserManager()
    alice, bob, carol = FakeConnection(), FakeConnection(), FakeConnection()
    send(manager, alice, MessageType.JOIN, name="Alice")
    send(manager, bob, MessageType.JOIN, name="Bob")
    send(manager, carol, MessageType.JOIN, name="Carol")

    send(manager, alice, MessageType.INVITE, target="Alice")
    assert latest(alice)["type"] == MessageType.ERROR.value
    send(manager, alice, MessageType.INVITE, target="Nobody")
    assert latest(alice)["type"] == MessageType.ERROR.value

    send(manager, alice, MessageType.INVITE, target="Bob")
    send(manager, carol, MessageType.INVITE, target="Alice")
    assert latest(carol)["type"] == MessageType.ERROR.value
    invite_id = latest(bob)["invite_id"]
    send(manager, bob, MessageType.INVITE_REJECT, invite_id=invite_id)
    assert latest(alice)["status"] == "REJECTED"


def test_disconnect_cancels_pending_invitation_and_updates_online_list() -> None:
    manager = UserManager()
    alice, bob = FakeConnection(), FakeConnection()
    send(manager, alice, MessageType.JOIN, name="Alice")
    send(manager, bob, MessageType.JOIN, name="Bob")
    send(manager, alice, MessageType.INVITE, target="Bob")

    assert manager.disconnect(bob) is True

    assert manager.get_online_players() == ["Alice"]
    assert latest(alice)["type"] == MessageType.PLAYER_LIST.value
    assert latest(alice)["players"] == ["Alice"]
    assert manager.get_player(alice).status is PlayerStatus.ONLINE
    assert manager.disconnect(bob) is False


def test_invite_requires_authenticated_connection() -> None:
    manager = UserManager()
    anonymous = FakeConnection()

    send(manager, anonymous, MessageType.INVITE, target="Alice")

    assert latest(anonymous)["type"] == MessageType.ERROR.value


def test_leave_removes_identity_and_stops_connection() -> None:
    manager = UserManager()
    connection = FakeConnection()
    send(manager, connection, MessageType.JOIN, name="Alice")

    send(manager, connection, MessageType.LEAVE)

    assert manager.get_online_players() == []
    assert connection.stopped is True