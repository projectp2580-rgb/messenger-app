from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

DB_PATH = os.environ.get("MESSENGER_DB", "messenger.db")
MAX_AVATAR_BYTES = 2 * 1024 * 1024
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@dataclass
class UserProfile:
    has_avatar: bool = False
    avatar: bytes | None = None
    avatar_mime: str | None = None
    bio: str = ""


def guess_mime(filename: str, content_type: str | None = None) -> str:
    if content_type and content_type in ALLOWED_MIME:
        return content_type
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(ext, "image/png")


class ProfileStore:
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
                CREATE TABLE IF NOT EXISTS user_profiles (
                    username TEXT PRIMARY KEY,
                    avatar BLOB,
                    avatar_mime TEXT,
                    bio TEXT NOT NULL DEFAULT ''
                )
            """)

    def get(self, username: str) -> UserProfile:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM user_profiles WHERE username=?", (username,)
            ).fetchone()
        if not row:
            return UserProfile()
        return UserProfile(
            has_avatar=row["avatar"] is not None,
            avatar=bytes(row["avatar"]) if row["avatar"] else None,
            avatar_mime=row["avatar_mime"],
            bio=row["bio"] or "",
        )

    def set_avatar(self, username: str, data: bytes, mime: str) -> None:
        if len(data) > MAX_AVATAR_BYTES:
            raise ValueError("Image is too large. Max size is 2 MB.")
        if mime not in ALLOWED_MIME:
            raise ValueError("Unsupported image format.")
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO user_profiles (username, avatar, avatar_mime, bio)
                VALUES (?, ?, ?, '')
                ON CONFLICT(username) DO UPDATE SET avatar=excluded.avatar,
                    avatar_mime=excluded.avatar_mime
                """,
                (username, data, mime),
            )

    def clear_avatar(self, username: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO user_profiles (username, avatar, avatar_mime, bio)
                VALUES (?, NULL, NULL, '')
                ON CONFLICT(username) DO UPDATE SET avatar=NULL, avatar_mime=NULL
                """,
                (username,),
            )

    def set_bio(self, username: str, bio: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO user_profiles (username, avatar, avatar_mime, bio)
                VALUES (?, NULL, NULL, ?)
                ON CONFLICT(username) DO UPDATE SET bio=excluded.bio
                """,
                (username, bio),
            )

    def rename(self, old_username: str, new_username: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE user_profiles SET username=? WHERE username=?",
                (new_username, old_username),
            )
