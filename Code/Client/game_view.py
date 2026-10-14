"""Canvas-based Caro board that renders, but never adjudicates, match state.

Win/lose overlay: shown after ROOM_UPDATE or GAME_END delivers a terminal
game_state.  The overlay is created once per terminal state; subsequent calls
to update_state() with the same game_state are ignored so the overlay never
duplicates.
"""

import math
import random
import tkinter as tk
import time
from collections.abc import Callable, Sequence
from tkinter import ttk
from types import SimpleNamespace
from typing import Any


class GameView(ttk.Frame):
    """Render authoritative board, turn and outcome fields from ROOM_UPDATE."""

    BOARD_SIZE = 15
    CELL_SIZE = 36
    MARGIN = 28
    CANVAS_SIZE = MARGIN * 2 + CELL_SIZE * BOARD_SIZE

    # Animation timing
    _BLINK_INTERVAL_MS = 420
    _CONFETTI_COUNT = 60
    _CONFETTI_INTERVAL_MS = 30
    _CONFETTI_STEPS = 80

    def __init__(self, master: tk.Misc, on_move: Callable[[str, int, int], None]) -> None:
        super().__init__(master, style="Panel.TFrame", padding=16)
        self._on_move = on_move
        self._room_id: str | None = None
        self._room_status = "WAITING"
        self._current_player: str | None = None
        self._own_piece: str | None = None
        self._game_state: str | None = None
        self._board: Sequence[Sequence[str]] | None = None
        self._live_board: Sequence[Sequence[str]] | None = None
        self._reviewing = False
        self._remaining_seconds: float | None = None
        self._clock_started_at: float | None = None
        self._clock_after_id: str | None = None
        self._winning_line: list[list[int]] = []
        self._winner: str | None = None
        self._loser: str | None = None
        self._reason: str | None = None
        # Track the terminal state we already showed an overlay for
        self._overlay_shown_for: str | None = None
        # Blink state
        self._blink_bright = True
        self._blink_after_id: str | None = None
        # Confetti particles: [x, y, vx, vy, color, size]
        self._confetti: list[list[Any]] = []
        self._confetti_step = 0
        self._confetti_after_id: str | None = None

        self.turn_var = tk.StringVar(self, value="Waiting for match start")
        self.piece_var = tk.StringVar(self, value="Your piece: --")
        self.result_var = tk.StringVar(self, value="")
        self.clock_var = tk.StringVar(self, value="")
        self._build()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        heading = ttk.Frame(self, style="Panel.TFrame")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, textvariable=self.turn_var, style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(heading, textvariable=self.piece_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e"
        )
        ttk.Label(heading, textvariable=self.clock_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="e"
        )

        self.canvas = tk.Canvas(
            self,
            width=self.CANVAS_SIZE,
            height=self.CANVAS_SIZE,
            background="#e7d8b3",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self._handle_board_click)

        ttk.Label(self, textvariable=self.result_var, style="Result.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self._draw_board()

    # -------------------------------------------------------------- public API

    def update_state(self, state: dict[str, Any], player_name: str) -> None:
        """Update the display from one server ROOM_UPDATE without local rule logic."""
        self._room_id = state.get("room_id")
        self._room_status = str(state.get("status", "WAITING"))
        self._current_player = state.get("current_player")
        remaining = state.get("remaining_seconds")
        self._remaining_seconds = (
            float(remaining) if isinstance(remaining, (int, float)) else None
        )
        self._clock_started_at = time.monotonic()
        new_game_state = state.get("game_state")
        board = state.get("board")
        self._board = board if isinstance(board, (list, tuple)) else None
        self._live_board = self._board
        self._reviewing = False
        self._start_clock()

        wl = state.get("winning_line")
        self._winning_line = wl if isinstance(wl, list) else []
        self._winner = state.get("winner")
        self._loser = state.get("loser")
        self._reason = state.get("reason")

        self._own_piece = None
        for seat in state.get("players", []):
            if str(seat.get("name", "")).casefold() == player_name.casefold():
                self._own_piece = seat.get("piece")
                break

        prev_game_state = self._game_state
        self._game_state = new_game_state

        self.piece_var.set(f"Your piece: {self._own_piece or '--'}")
        self.result_var.set(self._format_result())
        self.turn_var.set(self._format_turn())
        self._draw_board()

        # Show overlay once per terminal game state
        terminal = new_game_state in ("X_WON", "O_WON", "DRAW")
        if terminal and self._overlay_shown_for != new_game_state:
            self._overlay_shown_for = new_game_state
            self._stop_animations()
            self._show_end_overlay()
        elif terminal and not self.canvas.find_withtag("overlay"):
            self._show_end_overlay(animate=False)

        # Clear overlay if game restarted
        if prev_game_state in ("X_WON", "O_WON", "DRAW") and new_game_state == "IN_PROGRESS":
            self._overlay_shown_for = None
            self._stop_animations()

    def apply_game_end(self, message: dict[str, Any]) -> None:
        """Apply a GAME_END message, updating win/draw state if newer than current."""
        game_state = message.get("game_state")
        if game_state not in ("X_WON", "O_WON", "DRAW"):
            return
        wl = message.get("winning_line")
        self._winning_line = wl if isinstance(wl, list) else []
        self._winner = message.get("winner")
        self._loser = message.get("loser")
        self._reason = message.get("reason")
        self._game_state = game_state
        self._room_status = "ENDED"
        self._start_clock()

        self.result_var.set(self._format_result())
        self.turn_var.set(self._format_turn())
        self._draw_board()

        if self._overlay_shown_for != game_state:
            self._overlay_shown_for = game_state
            self._stop_animations()
            self._show_end_overlay()
        elif not self.canvas.find_withtag("overlay"):
            self._show_end_overlay(animate=False)

    # ------------------------------------------------------- format helpers

    def _format_turn(self) -> str:
        if self._room_status == "PAUSED":
            return "Match paused — waiting for reconnect"
        if self._room_status != "IN_PROGRESS" or self._current_player is None:
            return "Waiting for match start"
        if self._own_piece == self._current_player:
            return f"Your turn  /  {self._current_player}"
        return f"Opponent's turn  /  {self._current_player}"

    def _format_result(self) -> str:
        if self._game_state == "DRAW":
            return "HÒA!"
        if self._game_state not in ("X_WON", "O_WON"):
            return ""
        winner_piece = self._game_state[0]
        if self._own_piece is None:
            return f"{winner_piece} WINS"
        return "🎉 CHIẾN THẮNG!" if winner_piece == self._own_piece else "😢 BẠN ĐÃ THUA!"

    # ---------------------------------------------------------- board drawing

    def _cell_center(self, row: int, col: int) -> tuple[float, float]:
        half = self.CELL_SIZE / 2
        x = self.MARGIN + col * self.CELL_SIZE + half
        y = self.MARGIN + row * self.CELL_SIZE + half
        return x, y

    def _draw_board(self) -> None:
        self.canvas.delete("all")
        start = self.MARGIN
        end = self.MARGIN + self.CELL_SIZE * self.BOARD_SIZE
        for index in range(self.BOARD_SIZE + 1):
            point = self.MARGIN + index * self.CELL_SIZE
            self.canvas.create_line(start, point, end, point, fill="#604b31", width=1)
            self.canvas.create_line(point, start, point, end, fill="#604b31", width=1)

        if self._board is None:
            return

        # Build winning set for highlight
        win_set = {(int(c[0]), int(c[1])) for c in self._winning_line if len(c) >= 2}

        radius = self.CELL_SIZE * 0.32
        win_radius = self.CELL_SIZE * 0.38

        for row_index, row in enumerate(self._board[: self.BOARD_SIZE]):
            for col_index, piece in enumerate(row[: self.BOARD_SIZE]):
                if not piece or piece == " ":
                    continue
                x, y = self._cell_center(row_index, col_index)
                in_win = (row_index, col_index) in win_set
                r = win_radius if in_win else radius

                if piece == "X":
                    color = "#ffd700" if in_win else "#b94436"
                    lw = 5 if in_win else 4
                    self.canvas.create_line(
                        x - r, y - r, x + r, y + r,
                        fill=color, width=lw, capstyle=tk.ROUND,
                    )
                    self.canvas.create_line(
                        x + r, y - r, x - r, y + r,
                        fill=color, width=lw, capstyle=tk.ROUND,
                    )
                elif piece == "O":
                    color = "#ffd700" if in_win else "#176f72"
                    lw = 5 if in_win else 4
                    self.canvas.create_oval(
                        x - r, y - r, x + r, y + r,
                        outline=color, width=lw,
                    )

                if in_win and self._blink_bright:
                    glow = self.CELL_SIZE * 0.46
                    self.canvas.create_oval(
                        x - glow, y - glow, x + glow, y + glow,
                        outline="#ffd700", width=2, dash=(4, 4),
                    )

    # ---------------------------------------------------------- end overlay

    def _show_end_overlay(self, *, animate: bool = True) -> None:
        """Draw transparent-effect overlay and start animations."""
        cs = self.CANVAS_SIZE
        is_win = self._game_state in ("X_WON", "O_WON") and (
            self._own_piece is None
            or self._game_state[0] == self._own_piece
        )
        is_draw = self._game_state == "DRAW"

        # Semi-transparent overlay strip at top
        overlay_h = 110
        if is_win:
            bg = "#1a4a1a"
            title_text = "🎉  CHIẾN THẮNG!"
            title_color = "#ffd700"
            sub_color = "#a0f0a0"
        elif is_draw:
            bg = "#3a3a1a"
            title_text = "🤝  HÒA!"
            title_color = "#ffd700"
            sub_color = "#e0e0a0"
        else:
            bg = "#4a1a1a"
            title_text = "😢  BẠN ĐÃ THUA!"
            title_color = "#ff9999"
            sub_color = "#e0a0a0"

        self.canvas.create_rectangle(
            0, 0, cs, overlay_h,
            fill=bg, outline="", stipple="gray50",
            tags="overlay",
        )
        self.canvas.create_rectangle(
            0, 0, cs, overlay_h,
            fill=bg, outline="",
            tags="overlay",
        )

        self.canvas.create_text(
            cs / 2, 36,
            text=title_text,
            fill=title_color,
            font=("Segoe UI", 18, "bold"),
            tags="overlay",
        )

        if self._winner and not is_draw:
            winner_name = self._winner
            sub_text = f"Người chiến thắng: {winner_name}"
            if self._reason:
                sub_text = f"{winner_name} thắng!"
            self.canvas.create_text(
                cs / 2, 68,
                text=sub_text,
                fill=sub_color,
                font=("Segoe UI", 11),
                tags="overlay",
            )
        elif is_draw and self._reason:
            self.canvas.create_text(
                cs / 2, 68,
                text="Bàn cờ đầy, không có người thắng.",
                fill=sub_color,
                font=("Segoe UI", 11),
                tags="overlay",
            )

        if self._reason:
            self.canvas.create_text(
                cs / 2, 90,
                text=self._reason,
                fill=sub_color,
                font=("Segoe UI", 9),
                tags="overlay",
            )

        # Start blink animation for winning pieces
        if animate and self._winning_line:
            self._start_blink()

        # Launch confetti for wins
        if animate and is_win:
            self._launch_confetti()

    # -------------------------------------------------------- blink animation

    def _start_blink(self) -> None:
        self._blink_bright = True
        self._blink_tick()

    def _blink_tick(self) -> None:
        if self._game_state not in ("X_WON", "O_WON"):
            return
        self._blink_bright = not self._blink_bright
        # Redraw board (overlay preserved via tags)
        self.canvas.delete("piece")
        self.canvas.delete("glow")
        self._redraw_pieces_only()
        self._blink_after_id = self.after(self._BLINK_INTERVAL_MS, self._blink_tick)

    def _redraw_pieces_only(self) -> None:
        if self._board is None:
            return
        win_set = {(int(c[0]), int(c[1])) for c in self._winning_line if len(c) >= 2}
        radius = self.CELL_SIZE * 0.32
        win_radius = self.CELL_SIZE * 0.38

        for row_index, row in enumerate(self._board[: self.BOARD_SIZE]):
            for col_index, piece in enumerate(row[: self.BOARD_SIZE]):
                if not piece or piece == " ":
                    continue
                x, y = self._cell_center(row_index, col_index)
                in_win = (row_index, col_index) in win_set
                r = win_radius if in_win else radius

                if piece == "X":
                    color = "#ffd700" if in_win else "#b94436"
                    lw = 5 if in_win else 4
                    self.canvas.create_line(
                        x - r, y - r, x + r, y + r,
                        fill=color, width=lw, capstyle=tk.ROUND, tags="piece",
                    )
                    self.canvas.create_line(
                        x + r, y - r, x - r, y + r,
                        fill=color, width=lw, capstyle=tk.ROUND, tags="piece",
                    )
                elif piece == "O":
                    color = "#ffd700" if in_win else "#176f72"
                    lw = 5 if in_win else 4
                    self.canvas.create_oval(
                        x - r, y - r, x + r, y + r,
                        outline=color, width=lw, tags="piece",
                    )

                if in_win and self._blink_bright:
                    glow = self.CELL_SIZE * 0.46
                    self.canvas.create_oval(
                        x - glow, y - glow, x + glow, y + glow,
                        outline="#ffd700", width=2, dash=(4, 4), tags="glow",
                    )
        # Keep overlay on top
        self.canvas.tag_raise("overlay")

    # ------------------------------------------------------ confetti animation

    def _launch_confetti(self) -> None:
        cs = self.CANVAS_SIZE
        colors = ["#ff6b6b", "#ffd700", "#6bff6b", "#6bb3ff", "#ff6bff", "#fff", "#ff9900"]
        self._confetti = []
        for _ in range(self._CONFETTI_COUNT):
            x = random.uniform(cs * 0.15, cs * 0.85)
            y = random.uniform(-30, -5)
            vx = random.uniform(-2.5, 2.5)
            vy = random.uniform(2.5, 6.5)
            color = random.choice(colors)
            size = random.uniform(4, 9)
            self._confetti.append([x, y, vx, vy, color, size])
        self._confetti_step = 0
        self._confetti_tick()

    def _confetti_tick(self) -> None:
        if self._confetti_step >= self._CONFETTI_STEPS:
            self.canvas.delete("confetti")
            return
        self.canvas.delete("confetti")
        cs = self.CANVAS_SIZE
        gravity = 0.18
        for p in self._confetti:
            p[0] += p[2]
            p[1] += p[3]
            p[3] += gravity
            if p[1] > cs + 20:
                p[1] = random.uniform(-30, -5)
                p[0] = random.uniform(cs * 0.1, cs * 0.9)
                p[3] = random.uniform(2.5, 5.5)
            x, y, _, _, color, size = p
            angle = (self._confetti_step * 15 + id(p)) % 360
            # Draw as a small rotated rectangle
            rad = math.radians(angle)
            dx = size * math.cos(rad)
            dy = size * math.sin(rad)
            self.canvas.create_line(
                x - dx, y - dy, x + dx, y + dy,
                fill=color, width=max(2, int(size * 0.4)),
                tags="confetti", capstyle=tk.ROUND,
            )
        self.canvas.tag_raise("overlay")
        self.canvas.tag_raise("confetti")
        self._confetti_step += 1
        self._confetti_after_id = self.after(self._CONFETTI_INTERVAL_MS, self._confetti_tick)

    # ---------------------------------------------------- stop all animations

    def _stop_animations(self) -> None:
        if self._blink_after_id is not None:
            try:
                self.after_cancel(self._blink_after_id)
            except Exception:
                pass
            self._blink_after_id = None
        if self._confetti_after_id is not None:
            try:
                self.after_cancel(self._confetti_after_id)
            except Exception:
                pass
            self._confetti_after_id = None
        self.canvas.delete("confetti")
        self.canvas.delete("overlay")
        self.canvas.delete("glow")

    # --------------------------------------------------------- click handling

    def _handle_board_click(self, event: tk.Event[tk.Misc] | SimpleNamespace) -> None:
        if self._reviewing or self._room_status != "IN_PROGRESS" or not self._room_id:
            return
        if self._own_piece is None or self._current_player != self._own_piece:
            return
        if event.x < self.MARGIN or event.y < self.MARGIN:
            return
        col = int((event.x - self.MARGIN) // self.CELL_SIZE)
        row = int((event.y - self.MARGIN) // self.CELL_SIZE)
        if not (0 <= row < self.BOARD_SIZE and 0 <= col < self.BOARD_SIZE):
            return
        if self._board is not None:
            if row < len(self._board) and col < len(self._board[row]):
                if self._board[row][col] != " ":
                    return
        self._on_move(self._room_id, row, col)

    def show_review_board(self, board: Sequence[Sequence[str]] | None) -> None:
        """Render a local history preview without changing the live room state."""
        self._reviewing = board is not None
        self._board = board if board is not None else self._live_board
        self._draw_board()

    def _start_clock(self) -> None:
        if self._clock_after_id is not None:
            try:
                self.after_cancel(self._clock_after_id)
            except tk.TclError:
                pass
            self._clock_after_id = None
        if self._room_status in ("IN_PROGRESS", "PAUSED") and self._remaining_seconds is not None:
            self._tick_clock()
        else:
            self.clock_var.set("")

    def _tick_clock(self) -> None:
        if self._remaining_seconds is None:
            self.clock_var.set("")
            return
        elapsed = 0.0
        if self._room_status == "IN_PROGRESS" and self._clock_started_at is not None:
            elapsed = time.monotonic() - self._clock_started_at
        remaining = max(0, int(self._remaining_seconds - elapsed + 0.999))
        self.clock_var.set(f"Turn time: {remaining}s")
        if self._room_status == "IN_PROGRESS":
            self._clock_after_id = self.after(1000, self._tick_clock)