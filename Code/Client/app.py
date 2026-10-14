"""Tk application controller; all widget updates run on the Tk main thread."""

import logging
import queue
import tkinter as tk
import time
from tkinter import ttk
from typing import Any

from Code.Client.lobby_view import LobbyView
from Code.Client.login_view import LoginView
from Code.Client.network_client import NetworkClient, NetworkEvent
from Code.Client.room_view import RoomView
from Code.Shared.message_types import MessageType

logger = logging.getLogger(__name__)


class App:
    """Own screen transitions, queue polling and server-command routing."""

    def __init__(self, root: tk.Tk, network_client: NetworkClient | None = None) -> None:
        self.root = root
        self.events: queue.Queue[NetworkEvent] = queue.Queue()
        self.network = network_client or NetworkClient(self.events)
        self.player_name: str | None = None
        self._pending_name: str | None = None
        self._join_sent = False
        self._logged_in = False
        self._pending_room_action = False
        self._leaving_room = False
        self._logout_pending = False
        self._resume_token: str | None = None
        self._server_host: str | None = None
        self._server_port: int | None = None
        self._reconnect_deadline: float | None = None
        self._reconnect_after_id: str | None = None
        self.active_room_id: str | None = None
        self._latest_room_state: dict[str, Any] | None = None
        self._room_versions: dict[str, int] = {}
        self.login_view: LoginView | None = None
        self.lobby_view: LobbyView | None = None
        self.room_view: RoomView | None = None
        self._closing = False
        self._after_id: str | None = None

        self._configure_window()
        self._build_shell()
        self._show_login()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = self.root.after(40, self._poll_events)

    def _configure_window(self) -> None:
        self.root.title("Caro Online")
        self.root.geometry("1120x790")
        self.root.minsize(920, 680)
        self.root.configure(background="#eeeee7")
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("App.TFrame", background="#eeeee7")
        style.configure("Panel.TFrame", background="#fffef9")
        style.configure("TLabel", background="#eeeee7", foreground="#202f2a", font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background="#fffef9", foreground="#202f2a", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#eeeee7", foreground="#183d37", font=("Georgia", 23, "bold"))
        style.configure("Eyebrow.TLabel", background="#eeeee7", foreground="#b44b39", font=("Segoe UI", 9, "bold"))
        style.configure("Muted.TLabel", background="#eeeee7", foreground="#66736d", font=("Segoe UI", 10))
        style.configure("Field.TLabel", background="#eeeee7", foreground="#40534b", font=("Segoe UI", 9, "bold"))
        style.configure("Section.TLabel", background="#fffef9", foreground="#183d37", font=("Segoe UI", 11, "bold"))
        style.configure("Body.TLabel", background="#fffef9", foreground="#202f2a", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#eeeee7", foreground="#53655e", font=("Segoe UI", 9))
        style.configure("Connected.TLabel", background="#eeeee7", foreground="#167064", font=("Segoe UI", 9, "bold"))
        style.configure("Disconnected.TLabel", background="#eeeee7", foreground="#b44b39", font=("Segoe UI", 9, "bold"))
        style.configure("Error.TLabel", background="#eeeee7", foreground="#b13d31", font=("Segoe UI", 9, "bold"))
        style.configure("Result.TLabel", background="#fffef9", foreground="#b44b39", font=("Segoe UI", 12, "bold"))
        style.configure("Accent.TButton", background="#176f72", foreground="#ffffff", padding=(12, 8), font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#125b5e"), ("disabled", "#9aa9a3")])
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 9))
        style.configure("Treeview", rowheight=29, font=("Segoe UI", 9), background="#fffef9", fieldbackground="#fffef9")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_shell(self) -> None:
        self.shell = ttk.Frame(self.root, style="App.TFrame")
        self.shell.pack(fill="both", expand=True)
        self.shell.columnconfigure(0, weight=1)
        self.shell.rowconfigure(1, weight=1)
        header = ttk.Frame(self.shell, style="App.TFrame", padding=(22, 13))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="CARO  /  ONLINE", style="Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.connection_var = tk.StringVar(self.root, value="DISCONNECTED")
        self.connection_label = ttk.Label(
            header,
            textvariable=self.connection_var,
            style="Disconnected.TLabel",
        )
        self.connection_label.grid(row=0, column=1, sticky="e")
        self.content = ttk.Frame(self.shell, style="App.TFrame", padding=(18, 8, 18, 18))
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

    def _clear_content(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self.login_view = None
        self.lobby_view = None
        self.room_view = None

    def _show_login(self) -> None:
        self._clear_content()
        wrapper = ttk.Frame(self.content, style="App.TFrame")
        wrapper.place(relx=0.5, rely=0.45, anchor="center")
        self.login_view = LoginView(wrapper, self._begin_login)
        self.login_view.pack()

    def _show_lobby(self) -> None:
        self._clear_content()
        self.lobby_view = LobbyView(
            self.content,
            on_create_room=self._create_room,
            on_join_room=self._join_room,
            on_refresh_rooms=self._refresh_rooms,
            on_invite=self._invite_player,
            on_invite_response=self._respond_to_invite,
            on_logout=self._logout,
            on_watch_room=self._watch_room,
        )
        self.lobby_view.grid(row=0, column=0, sticky="nsew")
        if self.player_name:
            self.lobby_view.set_player_name(self.player_name)
        self.lobby_view.set_connection(self.network.connected)

    def _show_room(self, state: dict[str, Any]) -> None:
        self._clear_content()
        if self.player_name is None:
            return
        self.room_view = RoomView(
            self.content,
            self.player_name,
            on_start=self._start_match,
            on_leave=self._leave_room,
            on_move=self._make_move,
            on_chat=self._send_chat,
            on_surrender=self._surrender,
            on_replay=self._replay,
        )
        self.room_view.grid(row=0, column=0, sticky="nsew")
        self.room_view.update_state(state)

    def _begin_login(self, name: str, host: str, port: int) -> None:
        self._pending_name = name
        self._server_host = host
        self._server_port = port
        self._join_sent = False
        if self.network.connected:
            self._join_sent = self.network.send(self._join_message(name))
            if not self._join_sent and self.login_view is not None:
                self.login_view.set_busy(False)
                self.login_view.set_status("Could not send login request.", error=True)
            return
        try:
            self.network.connect(host, port)
        except (OSError, RuntimeError) as exc:
            if self.login_view is not None:
                self.login_view.set_busy(False)
                self.login_view.set_status(str(exc), error=True)

    def _poll_events(self) -> None:
        try:
            for _ in range(100):
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(event)
        except Exception:
            logger.exception("Error handling a client network event")
        finally:
            if not self._closing:
                self._after_id = self.root.after(40, self._poll_events)

    def _handle_event(self, event: NetworkEvent) -> None:
        if event.kind == "status":
            state = event.payload.get("state")
            connected = state == "CONNECTED"
            self.connection_var.set(str(state or "DISCONNECTED"))
            self.connection_label.configure(
                style="Connected.TLabel" if connected else "Disconnected.TLabel"
            )
            if connected and self._pending_name and not self._join_sent:
                self._join_sent = self.network.send(self._join_message(self._pending_name))
            elif state == "DISCONNECTED":
                reason = str(event.payload.get("reason", "Disconnected from server"))
                if self.login_view is not None:
                    self.login_view.set_busy(False)
                    self.login_view.set_status(reason, error=True)
                if self.lobby_view is not None:
                    self.lobby_view.set_connection(False, reason)
                if self.room_view is not None:
                    self.room_view.show_error(reason)
                if self._logout_pending:
                    self._finish_logout()
                elif self._logged_in and self._resume_token is not None:
                    self._schedule_reconnect(reason)
            return
        if event.kind == "error":
            self._show_error(str(event.payload.get("message", "Network error")))
            return
        if event.kind == "message":
            message = event.payload.get("message")
            if isinstance(message, dict):
                self._handle_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == MessageType.ERROR.value:
            if self._logged_in and self._reconnect_deadline is not None:
                self.network.close()
                return
            if not self._logged_in and self.login_view is not None:
                self._join_sent = False
                self.login_view.set_busy(False)
                self.login_view.set_status(str(message.get("message", "Request rejected")), error=True)
            else:
                self._show_error(str(message.get("message", "Request rejected")))
            return

        if message_type == MessageType.PLAYER_LIST.value:
            players = [str(name) for name in message.get("players", [])]
            resume_token = message.get("resume_token")
            if isinstance(resume_token, str) and resume_token:
                self._resume_token = resume_token
                self._reconnect_deadline = None
            if (
                not self._logged_in
                and self._join_sent
                and self._pending_name is not None
                and self._pending_name.casefold() in {name.casefold() for name in players}
            ):
                self.player_name = self._pending_name
                self._logged_in = True
                self._show_lobby()
                self.network.send({"type": MessageType.ROOM_LIST.value})
            if self.lobby_view is not None:
                self.lobby_view.set_players(players)
            return

        if message_type in (MessageType.ROOM_LIST.value, MessageType.ROOM_LIST_UPDATE.value):
            if self.lobby_view is not None:
                rooms = message.get("rooms", [])
                if isinstance(rooms, list):
                    self.lobby_view.set_rooms(rooms)
            return

        if message_type == MessageType.ROOM_UPDATE.value:
            self._handle_room_update(message)
            return

        if message_type == MessageType.CHAT.value:
            if self.room_view is not None:
                self.room_view.append_chat(message)
            return

        if message_type == MessageType.GAME_END.value:
            self._handle_game_end(message)
            return

        if message_type == MessageType.INVITE.value:
            if self.lobby_view is not None:
                self.lobby_view.prompt_invitation(
                    str(message.get("from", "A player")),
                    str(message.get("invite_id", "")),
                )
            return

        if message_type == MessageType.INVITE_RESULT.value and self.lobby_view is not None:
            self.lobby_view.show_notice(
                f"Invitation {message.get('status', '').lower()}: "
                f"{message.get('from', '')} -> {message.get('to', '')}"
            )

    def _handle_room_update(self, message: dict[str, Any]) -> None:
        room_id = str(message.get("room_id", ""))
        if not room_id:
            return
        if message.get("status") == "LEFT":
            if room_id == self.active_room_id:
                self._return_to_lobby()
            return
        if self._leaving_room and room_id == self.active_room_id:
            self._return_to_lobby()
            return
        if message.get("authority") != "SERVER":
            return
        version = message.get("version")
        if type(version) is not int or version <= self._room_versions.get(room_id, 0):
            return
        if self.active_room_id is None:
            if not self._pending_room_action:
                return
        elif room_id != self.active_room_id:
            return

        self._room_versions[room_id] = version
        if message.get("event") == "PLAYER_LEFT":
            self._return_to_lobby()
            return
        self.active_room_id = room_id
        self._pending_room_action = False
        self._latest_room_state = dict(message)
        if self.room_view is None:
            self._show_room(self._latest_room_state)
        elif self.room_view.room_id == room_id:
            self.room_view.update_state(self._latest_room_state)
        if message.get("event") == "PLAYER_DISCONNECTED" and self.room_view is not None:
            player = message.get("disconnected_player", "A player")
            self.room_view.show_error(f"{player} disconnected")

    def _handle_game_end(self, message: dict[str, Any]) -> None:
        """Forward authoritative GAME_END to the active game view."""
        room_id = str(message.get("room_id", ""))
        if not room_id or room_id != self.active_room_id:
            return
        if self.room_view is None:
            return
        self.room_view.game_view.apply_game_end(message)

    def _show_error(self, message: str) -> None:
        if self.room_view is not None:
            self.room_view.show_error(message)
        elif self.lobby_view is not None:
            self.lobby_view.show_notice(message, error=True)
        elif self.login_view is not None:
            self.login_view.set_busy(False)
            self.login_view.set_status(message, error=True)

    def _create_room(self) -> None:
        self._pending_room_action = True
        if not self.network.send({"type": MessageType.CREATE_ROOM.value}):
            self._pending_room_action = False
            self._show_error("Not connected to server")

    def _join_room(self, room_id: str) -> None:
        self._pending_room_action = True
        if not self.network.send({"type": MessageType.JOIN_ROOM.value, "room_id": room_id}):
            self._pending_room_action = False
            self._show_error("Not connected to server")

    def _refresh_rooms(self) -> None:
        if not self.network.send({"type": MessageType.ROOM_LIST.value}):
            self._show_error("Not connected to server")

    def _invite_player(self, target: str) -> None:
        if not self.network.send({"type": MessageType.INVITE.value, "target": target}):
            self._show_error("Not connected to server")

    def _respond_to_invite(self, invite_id: str, accepted: bool) -> None:
        message_type = MessageType.INVITE_ACCEPT if accepted else MessageType.INVITE_REJECT
        self.network.send({"type": message_type.value, "invite_id": invite_id})

    def _logout(self) -> None:
        """Ask the server to remove this identity, then return to Login."""
        if not self.network.connected:
            self._finish_logout()
            return
        self._logout_pending = True
        if not self.network.send({"type": MessageType.LEAVE.value}):
            self.network.close()
            self._finish_logout()
        elif self.lobby_view is not None:
            self.lobby_view.show_notice("Disconnecting...")

    def _finish_logout(self) -> None:
        self._logout_pending = False
        self._logged_in = False
        self._join_sent = False
        self._pending_name = None
        self.player_name = None
        self._resume_token = None
        self._reconnect_deadline = None
        if self._reconnect_after_id is not None:
            self.root.after_cancel(self._reconnect_after_id)
            self._reconnect_after_id = None
        self._pending_room_action = False
        self._leaving_room = False
        self.active_room_id = None
        self._latest_room_state = None
        self._room_versions.clear()
        self._show_login()
        if self.login_view is not None:
            self.login_view.set_status("Disconnected")

    def _start_match(self, room_id: str) -> None:
        self.network.send({"type": MessageType.GAME_START.value, "room_id": room_id})

    def _watch_room(self, room_id: str) -> None:
        self._pending_room_action = True
        if not self.network.send(
            {"type": MessageType.WATCH_ROOM.value, "room_id": room_id}
        ):
            self._pending_room_action = False
            self._show_error("Not connected to server")

    def _send_chat(self, room_id: str, content: str) -> None:
        if not self.network.send(
            {"type": MessageType.CHAT.value, "room_id": room_id, "content": content}
        ):
            self._show_error("Chat not sent: disconnected")

    def _surrender(self, room_id: str) -> None:
        self.network.send({"type": MessageType.SURRENDER.value, "room_id": room_id})

    def _replay(self, room_id: str, accepted: bool) -> None:
        self.network.send(
            {
                "type": MessageType.PLAY_AGAIN.value,
                "room_id": room_id,
                "accepted": accepted,
            }
        )

    def _join_message(self, name: str) -> dict[str, Any]:
        message: dict[str, Any] = {"type": MessageType.JOIN.value, "name": name}
        if self._resume_token is not None:
            message["resume_token"] = self._resume_token
        return message

    def _schedule_reconnect(self, reason: str) -> None:
        if self._reconnect_deadline is None:
            self._reconnect_deadline = time.monotonic() + 60.0
        if self.room_view is not None:
            self.room_view.show_error(f"Connection lost; reconnecting: {reason}")
        if self._reconnect_after_id is None:
            self._reconnect_after_id = self.root.after(500, self._try_reconnect)

    def _try_reconnect(self) -> None:
        self._reconnect_after_id = None
        if (
            self._closing
            or self._logout_pending
            or not self._logged_in
            or self._resume_token is None
            or self._pending_name is None
        ):
            return
        if self._reconnect_deadline is None or time.monotonic() >= self._reconnect_deadline:
            self._reconnect_deadline = None
            self._show_error("Reconnect window expired. Please sign in again.")
            return
        if self._server_host is None or self._server_port is None:
            self._show_error("Cannot reconnect: server address is unavailable.")
            return
        try:
            self._join_sent = False
            self.network.connect(self._server_host, self._server_port)
        except (OSError, RuntimeError):
            self._reconnect_after_id = self.root.after(500, self._try_reconnect)

    def _make_move(self, room_id: str, row: int, col: int) -> None:
        if not self.network.send(
            {"type": MessageType.MOVE.value, "room_id": room_id, "row": row, "col": col}
        ):
            self._show_error("Move not sent: disconnected")

    def _leave_room(self, room_id: str) -> None:
        self._leaving_room = True
        if not self.network.send({"type": MessageType.LEAVE_ROOM.value, "room_id": room_id}):
            self._leaving_room = False
            self._show_error("Not connected to server")

    def _return_to_lobby(self) -> None:
        self._leaving_room = False
        self._pending_room_action = False
        self.active_room_id = None
        self._latest_room_state = None
        if self._logged_in:
            self._show_lobby()
            self.network.send({"type": MessageType.ROOM_LIST.value})

    def stop(self) -> None:
        """Stop polling and request network shutdown without destroying the root."""
        if self._closing:
            return
        self._closing = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        if self._reconnect_after_id is not None:
            try:
                self.root.after_cancel(self._reconnect_after_id)
            except tk.TclError:
                pass
            self._reconnect_after_id = None
        self.network.close()

    def close(self) -> None:
        """Stop the application and destroy the Tk window."""
        self.stop()
        self.root.destroy()