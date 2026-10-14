"""Durable storage for completed Caro match rounds."""

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any


class MatchHistoryStore:
    """Persist completed match results and their move histories in SQLite."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS completed_matches (
                    room_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    host TEXT NOT NULL,
                    players_json TEXT NOT NULL,
                    winner TEXT,
                    loser TEXT,
                    game_state TEXT,
                    reason TEXT,
                    winning_piece TEXT,
                    winning_line_json TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    moves_json TEXT NOT NULL,
                    PRIMARY KEY (room_id, round_number)
                )
                """
            )

    def save_completed_match(
        self,
        *,
        room_id: str,
        round_number: int,
        host: str,
        players: list[dict[str, str]],
        winner: str | None,
        loser: str | None,
        game_state: str | None,
        reason: str | None,
        winning_piece: str | None,
        winning_line: list[list[int]] | None,
        started_at: str,
        ended_at: str,
        moves: list[dict[str, Any]],
    ) -> None:
        """Atomically save one round's result and all of its moves."""
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO completed_matches (
                    room_id, round_number, host, players_json, winner, loser,
                    game_state, reason, winning_piece, winning_line_json,
                    started_at, ended_at, moves_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    round_number,
                    host,
                    json.dumps(players),
                    winner,
                    loser,
                    game_state,
                    reason,
                    winning_piece,
                    json.dumps(winning_line) if winning_line is not None else None,
                    started_at,
                    ended_at,
                    json.dumps(moves),
                ),
            )

    def get_completed_matches(self) -> list[dict[str, Any]]:
        """Load persisted match results, including their complete move histories."""
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT room_id, round_number, host, players_json, winner, loser,
                       game_state, reason, winning_piece, winning_line_json,
                       started_at, ended_at, moves_json
                FROM completed_matches
                ORDER BY ended_at, room_id, round_number
                """
            ).fetchall()
        return [
            {
                "room_id": row[0],
                "round_number": row[1],
                "host": row[2],
                "players": json.loads(row[3]),
                "winner": row[4],
                "loser": row[5],
                "game_state": row[6],
                "reason": row[7],
                "winning_piece": row[8],
                "winning_line": json.loads(row[9]) if row[9] is not None else None,
                "started_at": row[10],
                "ended_at": row[11],
                "moves": json.loads(row[12]),
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=5.0)
