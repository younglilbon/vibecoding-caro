"""Lobby screen for online players, rooms and invitation actions."""

import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import messagebox, ttk
from typing import Any


class LobbyView(ttk.Frame):
    """Display lobby data supplied by App and emit user intents via callbacks."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_create_room: Callable[[], None],
        on_join_room: Callable[[str], None],
        on_refresh_rooms: Callable[[], None],
        on_invite: Callable[[str], None],
        on_invite_response: Callable[[str, bool], None],
        on_logout: Callable[[], None],
        on_watch_room: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, style="App.TFrame", padding=24)
        self._on_create_room = on_create_room
        self._on_join_room = on_join_room
        self._on_refresh_rooms = on_refresh_rooms
        self._on_invite = on_invite
        self._on_invite_response = on_invite_response
        self._on_logout = on_logout
        self._on_watch_room = on_watch_room
        self._player_names: dict[str, str] = {}
        self._rooms: dict[str, dict[str, Any]] = {}
        self.connection_var = tk.StringVar(self, value="DISCONNECTED")
        self.player_var = tk.StringVar(self, value="")
        self.notice_var = tk.StringVar(self, value="")
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="LOBBY", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.player_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e", padx=(0, 16)
        )
        self.connection_label = ttk.Label(
            header,
            textvariable=self.connection_var,
            style="Connected.TLabel",
        )
        self.connection_label.grid(row=0, column=2, sticky="e")
        self.logout_button = ttk.Button(
            header,
            text="Disconnect",
            command=self._on_logout,
        )
        self.logout_button.grid(row=0, column=3, sticky="e", padx=(14, 0))

        panels = ttk.Frame(self, style="App.TFrame")
        panels.grid(row=1, column=0, sticky="nsew")
        panels.columnconfigure(0, weight=1, uniform="lobby")
        panels.columnconfigure(1, weight=2, uniform="lobby")
        panels.rowconfigure(0, weight=1)

        players_panel = ttk.Frame(panels, style="Panel.TFrame", padding=16)
        players_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        players_panel.columnconfigure(0, weight=1)
        players_panel.rowconfigure(1, weight=1)
        ttk.Label(players_panel, text="ONLINE PLAYERS", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )
        self.players_tree = ttk.Treeview(
            players_panel,
            columns=("name",),
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.players_tree.heading("name", text="Player")
        self.players_tree.column("name", anchor="w", width=180)
        self.players_tree.grid(row=1, column=0, sticky="nsew")
        players_scroll = ttk.Scrollbar(
            players_panel,
            orient="vertical",
            command=self.players_tree.yview,
        )
        players_scroll.grid(row=1, column=1, sticky="ns")
        self.players_tree.configure(yscrollcommand=players_scroll.set)
        self.invite_button = ttk.Button(
            players_panel,
            text="Invite selected",
            command=self._invite_selected,
        )
        self.invite_button.grid(row=2, column=0, sticky="ew", pady=(12, 0))

        rooms_panel = ttk.Frame(panels, style="Panel.TFrame", padding=16)
        rooms_panel.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        rooms_panel.columnconfigure(0, weight=1)
        rooms_panel.rowconfigure(1, weight=1)
        room_header = ttk.Frame(rooms_panel, style="Panel.TFrame")
        room_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        room_header.columnconfigure(0, weight=1)
        ttk.Label(room_header, text="ROOMS", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(room_header, text="Refresh", command=self._on_refresh_rooms).grid(
            row=0, column=1, sticky="e"
        )
        self.rooms_tree = ttk.Treeview(
            rooms_panel,
            columns=("host", "players", "status"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        for column, label, width in (
            ("host", "Host", 130),
            ("players", "Players", 90),
            ("status", "Status", 140),
        ):
            self.rooms_tree.heading(column, text=label)
            self.rooms_tree.column(column, anchor="w", width=width)
        self.rooms_tree.grid(row=1, column=0, columnspan=2, sticky="nsew")
        rooms_scroll = ttk.Scrollbar(
            rooms_panel,
            orient="vertical",
            command=self.rooms_tree.yview,
        )
        rooms_scroll.grid(row=1, column=2, sticky="ns")
        self.rooms_tree.configure(yscrollcommand=rooms_scroll.set)
        self.create_button = ttk.Button(
            rooms_panel,
            text="Create room",
            style="Accent.TButton",
            command=self._on_create_room,
        )
        rooms_panel.columnconfigure(0, weight=1, uniform="room-actions")
        rooms_panel.columnconfigure(1, weight=1, uniform="room-actions")
        rooms_panel.columnconfigure(2, weight=1, uniform="room-actions")
        self.create_button.grid(row=2, column=0, sticky="ew", pady=(12, 0), padx=(0, 4))
        self.join_button = ttk.Button(
            rooms_panel,
            text="Join selected",
            command=self._join_selected,
        )
        self.join_button.grid(row=2, column=1, sticky="ew", pady=(12, 0), padx=4)
        self.watch_button = ttk.Button(
            rooms_panel,
            text="Watch selected",
            command=self._watch_selected,
        )
        self.watch_button.grid(row=2, column=2, sticky="ew", pady=(12, 0), padx=(4, 0))

        self.notice_label = ttk.Label(self, textvariable=self.notice_var, style="Status.TLabel")
        self.notice_label.grid(row=2, column=0, sticky="w", pady=(14, 0))

    def set_player_name(self, name: str) -> None:
        self.player_var.set(f"SIGNED IN AS  {name}")

    def set_connection(self, connected: bool, detail: str = "") -> None:
        self.connection_var.set("CONNECTED" if connected else "DISCONNECTED")
        self.connection_label.configure(
            style="Connected.TLabel" if connected else "Disconnected.TLabel"
        )
        if detail:
            self.notice_var.set(detail)

    def set_players(self, players: Sequence[str]) -> None:
        self.players_tree.delete(*self.players_tree.get_children())
        self._player_names.clear()
        for index, name in enumerate(players):
            item_id = f"player-{index}"
            self._player_names[item_id] = name
            self.players_tree.insert("", "end", iid=item_id, values=(name,))

    def set_rooms(self, rooms: Sequence[dict[str, Any]]) -> None:
        self.rooms_tree.delete(*self.rooms_tree.get_children())
        self._rooms.clear()
        for room in rooms:
            room_id = str(room.get("room_id", ""))
            if not room_id:
                continue
            self._rooms[room_id] = dict(room)
            players = room.get("players", [])
            player_count = f"{len(players)}/{room.get('capacity', 2)}"
            spectator_count = int(room.get("spectator_count", 0))
            if spectator_count:
                player_count += f" (+{spectator_count} watching)"
            self.rooms_tree.insert(
                "",
                "end",
                iid=room_id,
                values=(room.get("host", ""), player_count, room.get("status", "")),
            )

    def show_notice(self, text: str, *, error: bool = False) -> None:
        self.notice_var.set(text)
        self.notice_label.configure(style="Error.TLabel" if error else "Status.TLabel")

    def prompt_invitation(self, player_name: str, invite_id: str) -> None:
        accepted = messagebox.askyesno(
            "Game invitation",
            f"{player_name} invited you to play. Accept?",
            parent=self.winfo_toplevel(),
        )
        self._on_invite_response(invite_id, accepted)

    def _invite_selected(self) -> None:
        selection = self.players_tree.selection()
        if selection:
            self._on_invite(self._player_names[selection[0]])

    def _join_selected(self) -> None:
        selection = self.rooms_tree.selection()
        if selection and self._rooms.get(selection[0], {}).get("status") == "WAITING":
            self._on_join_room(selection[0])

    def _watch_selected(self) -> None:
        selection = self.rooms_tree.selection()
        if selection and self._on_watch_room is not None:
            self._on_watch_room(selection[0])