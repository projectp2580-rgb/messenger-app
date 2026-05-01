from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass

DB_PATH = os.environ.get("MESSENGER_DB", "messenger.db")


@dataclass
class Broadcast:
    id: str
    author: str
    text: str
    ts: float


class BroadcastStore:
    def __init__(self, db_path: str = DB_PATH):
        self._db = db_path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id TEXT PRIMARY KEY,
                    author TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts REAL NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_dismissals (
                    username TEXT NOT NULL,
                    broadcast_id TEXT NOT NULL,
                    PRIMARY KEY (username, broadcast_id)
                )
            """)

    def submit(self, author: str, text: str) -> Broadcast:
        if not text or not text.strip():
            raise ValueError("Broadcast text cannot be empty.")
        bc = Broadcast(
            id=str(uuid.uuid4()),
            author=author,
            text=text.strip(),
            ts=time.time(),
        )
        with self._conn() as c:
            c.execute(
                "INSERT INTO broadcasts VALUES (?,?,?,?)",
                (bc.id, bc.author, bc.text, bc.ts),
            )
        return bc

    def list_recent(self, limit: int = 20) -> list[Broadcast]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM broadcasts ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Broadcast(id=r["id"], author=r["author"], text=r["text"], ts=r["ts"])
            for r in rows
        ]

    def delete(self, broadcast_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM broadcasts WHERE id=?", (broadcast_id,))
            c.execute(
                "DELETE FROM broadcast_dismissals WHERE broadcast_id=?",
                (broadcast_id,),
            )

    def latest_unread_for(self, username: str) -> Broadcast | None:
        with self._conn() as c:
            row = c.execute(
                """
                SELECT b.* FROM broadcasts b
                WHERE b.author != ? AND b.id NOT IN (
                    SELECT broadcast_id FROM broadcast_dismissals
                    WHERE username=?
                )
                ORDER BY b.ts DESC LIMIT 1
                """,
                (username, username),
            ).fetchone()
        if not row:
            return None
        return Broadcast(
            id=row["id"], author=row["author"], text=row["text"], ts=row["ts"]
        )

    def dismiss(self, username: str, broadcast_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO broadcast_dismissals VALUES (?,?)",
                (username, broadcast_id),
            )
