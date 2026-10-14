"""Room screen composing authoritative room metadata and the Caro board."""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from Code.Client.game_view import GameView


class RoomView(ttk.Frame):
    """Display room membership and delegate start, leave and move intents."""

    def __init__(
        self,
        master: tk.Misc,
        player_name: str,
        *,
        on_start: Callable[[str], None],
        on_leave: Callable[[str], None],
        on_move: Callable[[str, int, int], None],
        on_chat: Callable[[str, str], None] | None = None,
        on_surrender: Callable[[str], None] | None = None,
        on_replay: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__(master, style="App.TFrame", padding=22)
        self.player_name = player_name
        self._on_start = on_start
        self._on_leave = on_leave
        self._on_chat = on_chat
        self._on_surrender = on_surrender
        self._on_replay = on_replay
        self.room_id: str | None = None
        self._room_state: dict[str, Any] = {}
        self._history: list[dict[str, Any]] = []
        self.room_title_var = tk.StringVar(self, value="ROOM")
        self.host_var = tk.StringVar(self, value="")
        self.players_var = tk.StringVar(self, value="")
        self.status_var = tk.StringVar(self, value="Waiting for room state")
        self.waiting_var = tk.StringVar(self, value="Waiting for room state")
        self.chat_var = tk.StringVar(self, value="")
        self._build(on_move)

    def _build(self, on_move: Callable[[str, int, int], None]) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)
        header = ttk.Frame(self, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, textvariable=self.room_title_var, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, sticky="e", padx=(12, 16)
        )
        ttk.Button(header, text="Leave room", command=self._leave).grid(
            row=0, column=2, sticky="e"
        )

        body = ttk.Frame(self, style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        side = ttk.Frame(body, style="Panel.TFrame", padding=16)
        side.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        ttk.Label(side, text="MATCH DETAILS", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 16)
        )
        ttk.Label(side, text="HOST", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Label(side, textvariable=self.host_var, style="Body.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 18)
        )
        ttk.Label(side, text="PLAYERS", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            side,
            textvariable=self.players_var,
            style="Body.TLabel",
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(0, 22))
        self.start_button = ttk.Button(
            side,
            text="Start match",
            style="Accent.TButton",
            command=self._start,
            state="disabled",
        )
        self.start_button.grid(row=5, column=0, sticky="ew")
        self.surrender_button = ttk.Button(
            side, text="Surrender", command=self._surrender, state="disabled"
        )
        self.surrender_button.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        replay_actions = ttk.Frame(side, style="Panel.TFrame")
        replay_actions.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        self.replay_button = ttk.Button(
            replay_actions, text="Play again", command=lambda: self._replay(True)
        )
        self.replay_button.pack(side="left", fill="x", expand=True)
        self.decline_replay_button = ttk.Button(
            replay_actions, text="Decline", command=lambda: self._replay(False)
        )
        self.decline_replay_button.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Label(side, text="MOVE HISTORY", style="Field.TLabel").grid(
            row=8, column=0, sticky="w", pady=(12, 4)
        )
        self.history_list = tk.Listbox(side, height=7, width=29, exportselection=False)
        self.history_list.grid(row=9, column=0, sticky="ew")
        self.history_list.bind("<<ListboxSelect>>", self._review_selected)
        ttk.Button(side, text="Live board", command=self._show_live_board).grid(
            row=10, column=0, sticky="ew", pady=(4, 0)
        )

        self.match_area = ttk.Frame(body, style="App.TFrame")
        self.match_area.grid(row=0, column=1, sticky="nsew")
        self.match_area.columnconfigure(0, weight=1)
        self.match_area.rowconfigure(0, weight=1)
        self.waiting_panel = ttk.Frame(self.match_area, style="Panel.TFrame", padding=24)
        self.waiting_panel.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            self.waiting_panel,
            textvariable=self.waiting_var,
            style="Section.TLabel",
            justify="center",
        ).place(relx=0.5, rely=0.5, anchor="center")
        self.game_view = GameView(self.match_area, on_move)
        self.game_view.grid(row=0, column=0, sticky="nsew")
        self.game_view.grid_remove()

        chat_panel = ttk.Frame(self, style="Panel.TFrame", padding=(10, 6))
        chat_panel.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        chat_panel.columnconfigure(0, weight=1)
        self.chat_log = tk.Text(chat_panel, height=4, state="disabled", wrap="word")
        self.chat_log.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.chat_entry = ttk.Entry(chat_panel, textvariable=self.chat_var)
        self.chat_entry.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.chat_entry.bind("<Return>", self._submit_chat)
        ttk.Button(chat_panel, text="Send", command=self._submit_chat).grid(
            row=1, column=1, padx=(6, 0), pady=(5, 0)
        )

    def update_state(self, state: dict[str, Any]) -> None:
        """Render a server ROOM_UPDATE; no local match outcome is calculated."""
        self._room_state = dict(state)
        self.room_id = state.get("room_id")
        self.room_title_var.set(f"ROOM  /  {str(self.room_id)[:8]}")
        status = str(state.get("status", "UNKNOWN"))
        self.status_var.set(status.replace("_", " "))
        if status == "WAITING":
            self.waiting_var.set("Waiting for an opponent to join")
            self.waiting_panel.grid()
            self.game_view.grid_remove()
        elif status == "READY":
            self.waiting_var.set("Opponent connected. The host can start the match.")
            self.waiting_panel.grid()
            self.game_view.grid_remove()
        else:
            self.waiting_panel.grid_remove()
            self.game_view.grid()
        self.host_var.set(str(state.get("host", "-")))
        players = state.get("players", [])
        self.players_var.set(
            "\n".join(
                f"{player.get('piece', '?')}   {player.get('name', 'Unknown')}"
                for player in players
            ) or "No players"
        )
        is_host = str(state.get("host", "")).casefold() == self.player_name.casefold()
        can_start = is_host and status == "READY"
        self.start_button.configure(state="normal" if can_start else "disabled")
        own_seat = any(
            str(player.get("name", "")).casefold() == self.player_name.casefold()
            for player in players
        )
        self.surrender_button.configure(
            state="normal" if own_seat and status == "IN_PROGRESS" else "disabled"
        )
        self.replay_button.configure(
            state="normal" if own_seat and status == "ENDED" else "disabled"
        )
        self.decline_replay_button.configure(
            state=(
                "normal"
                if own_seat
                and status == "ENDED"
                and bool(state.get("replay_requests"))
                else "disabled"
            )
        )
        self._history = [dict(move) for move in state.get("history", []) if isinstance(move, dict)]
        self.history_list.delete(0, "end")
        for move in self._history:
            self.history_list.insert(
                "end",
                f"{move.get('number')}. {move.get('piece')} {move.get('player')} "
                f"({move.get('row')}, {move.get('col')}) [R{move.get('round', 1)}]",
            )
        self.game_view.update_state(state, self.player_name)

    def show_error(self, message: str) -> None:
        self.status_var.set(message)

    def _start(self) -> None:
        if self.room_id is not None:
            self._on_start(self.room_id)

    def _leave(self) -> None:
        if self.room_id is not None:
            self._on_leave(self.room_id)

    def _surrender(self) -> None:
        if self.room_id is not None and self._on_surrender is not None:
            self._on_surrender(self.room_id)

    def _replay(self, accepted: bool) -> None:
        if self.room_id is not None and self._on_replay is not None:
            self._on_replay(self.room_id, accepted)

    def _submit_chat(self, _event: tk.Event[tk.Misc] | None = None) -> str:
        content = self.chat_var.get()
        if self.room_id is not None and self._on_chat is not None and content.strip():
            self._on_chat(self.room_id, content)
            self.chat_var.set("")
        return "break"

    def append_chat(self, message: dict[str, Any]) -> None:
        sender = str(message.get("sender", ""))
        content = str(message.get("content", ""))
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"{sender}: {content}\n")
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    def _review_selected(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self.history_list.curselection()
        if not selection:
            return
        board = [[" " for _ in range(15)] for _ in range(15)]
        selected = self._history[selection[0]]
        selected_round = selected.get("round", 1)
        round_history = [
            move for move in self._history if move.get("round", 1) == selected_round
        ]
        selected_in_round = round_history.index(selected)
        for move in round_history[: selected_in_round + 1]:
            row, col = move.get("row"), move.get("col")
            if type(row) is int and type(col) is int and 0 <= row < 15 and 0 <= col < 15:
                board[row][col] = str(move.get("piece", " "))
        self.game_view.show_review_board(board)

    def _show_live_board(self) -> None:
        self.game_view.show_review_board(None)