"""Tk tests for Lobby view selection and action callbacks."""

import tkinter as tk

from Code.Client.lobby_view import LobbyView


def make_lobby(root: tk.Tk, actions: dict[str, list]) -> LobbyView:
    return LobbyView(
        root,
        on_create_room=lambda: actions["create"].append(True),
        on_join_room=lambda room_id: actions["join"].append(room_id),
        on_refresh_rooms=lambda: actions["refresh"].append(True),
        on_invite=lambda name: actions["invite"].append(name),
        on_invite_response=lambda invite_id, accepted: actions["response"].append(
            (invite_id, accepted)
        ),
        on_logout=lambda: actions["logout"].append(True),
        on_watch_room=lambda room_id: actions["watch"].append(room_id),
    )


def test_lobby_displays_data_and_emits_selected_actions(tk_root: tk.Tk) -> None:
    root = tk_root
    actions = {key: [] for key in ("create", "join", "refresh", "invite", "response", "logout", "watch")}
    lobby = make_lobby(root, actions)
    lobby.pack()
    lobby.set_player_name("Alice")
    lobby.set_connection(True)
    lobby.set_players(["Alice", "Bob"])
    lobby.set_rooms(
        [{"room_id": "room-1", "host": "Bob", "players": [{}, {}], "capacity": 2, "status": "WAITING"}]
    )
    lobby.players_tree.selection_set("player-1")
    lobby.rooms_tree.selection_set("room-1")

    lobby.invite_button.invoke()
    lobby.join_button.invoke()
    lobby.watch_button.invoke()
    lobby.create_button.invoke()
    lobby.logout_button.invoke()
    lobby._on_refresh_rooms()

    assert lobby.player_var.get() == "SIGNED IN AS  Alice"
    assert lobby.connection_var.get() == "CONNECTED"
    assert actions["invite"] == ["Bob"]
    assert actions["join"] == ["room-1"]
    assert actions["watch"] == ["room-1"]
    assert actions["create"] == [True]
    assert actions["refresh"] == [True]
    assert actions["logout"] == [True]
    join_count = len(actions["join"])
    invite_count = len(actions["invite"])
    lobby.rooms_tree.selection_remove(lobby.rooms_tree.selection())
    lobby.players_tree.selection_remove(lobby.players_tree.selection())
    lobby.join_button.invoke()
    lobby.invite_button.invoke()

    assert len(actions["join"]) == join_count
    assert len(actions["invite"]) == invite_count
