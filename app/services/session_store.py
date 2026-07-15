"""SQLite persistence and lightweight lexical RAG for long-running sessions."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import settings

_lock = RLock()


def _db_path() -> Path:
    path = Path(settings.session_db_path).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path(), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_kind_updated
            ON sessions(kind, updated_at DESC);
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            speaker TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS memories_session
            ON memories(session_id, id DESC);
        CREATE TABLE IF NOT EXISTS model_ratings (
            model_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
    return connection


def _json_default(value: Any):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (set, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value)!r}")


def save_session(kind: str, session: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    clean = {key: value for key, value in session.items() if key != "running_task"}
    payload = json.dumps(clean, ensure_ascii=False, default=_json_default)
    session_id = clean["session_id"]
    title = clean.get("topic") or clean.get("title") or session_id
    with _lock, _connect() as db:
        db.execute(
            """
            INSERT INTO sessions(session_id, kind, title, status, payload, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
              title=excluded.title, status=excluded.status,
              payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (session_id, kind, title, clean.get("status", "idle"), payload,
             clean.get("created_at", now), now),
        )


def load_session(session_id: str, kind: str | None = None) -> dict | None:
    with _lock, _connect() as db:
        if kind:
            row = db.execute(
                "SELECT payload FROM sessions WHERE session_id=? AND kind=?",
                (session_id, kind),
            ).fetchone()
        else:
            row = db.execute("SELECT payload FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def list_sessions(kind: str, limit: int = 50) -> list[dict]:
    with _lock, _connect() as db:
        rows = db.execute(
            """SELECT session_id, kind, title, status, created_at, updated_at,
                      json_array_length(json_extract(payload, '$.messages')) AS message_count
               FROM sessions WHERE kind=? ORDER BY updated_at DESC LIMIT ?""",
            (kind, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def add_memory(session_id: str, kind: str, speaker: str, text: str) -> None:
    if not text.strip():
        return
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO memories(session_id, kind, speaker, text, created_at) VALUES(?, ?, ?, ?, ?)",
            (session_id, kind, speaker, text, datetime.now(timezone.utc).isoformat()),
        )


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-zäöüß]{4,}", text.lower())
        if token not in {"dass", "diese", "einer", "eines", "nicht", "sich", "wird", "haben", "eine"}
    }


def retrieve_memories(session_id: str, query: str, limit: int = 5) -> list[str]:
    """Retrieve semantically related snippets using lexical overlap.

    This is intentionally local and dependency-free. It gives the generator
    long-term context outside the short chat window and explicit examples it
    must not repeat.
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    with _lock, _connect() as db:
        rows = db.execute(
            "SELECT speaker, text FROM memories WHERE session_id=? ORDER BY id DESC LIMIT 300",
            (session_id,),
        ).fetchall()
    ranked: list[tuple[float, str]] = []
    for row in rows:
        candidate_tokens = _tokens(row["text"])
        if not candidate_tokens:
            continue
        overlap = len(query_tokens & candidate_tokens)
        if overlap == 0:
            continue
        score = overlap / max(1, len(query_tokens | candidate_tokens))
        ranked.append((score, f'{row["speaker"]}: {row["text"][:700]}'))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in ranked[:limit]]


def rate_model(model_id: str, kind: str, rating: int, note: str = "") -> None:
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO model_ratings(model_id, kind, rating, note, created_at) VALUES(?, ?, ?, ?, ?)",
            (model_id, kind, rating, note, datetime.now(timezone.utc).isoformat()),
        )


def model_rating_summary(model_id: str, kind: str) -> dict:
    with _lock, _connect() as db:
        row = db.execute(
            "SELECT COUNT(*) count, ROUND(AVG(rating), 2) average FROM model_ratings WHERE model_id=? AND kind=?",
            (model_id, kind),
        ).fetchone()
    return {"count": row["count"], "average": row["average"]}
