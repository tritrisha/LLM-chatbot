import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, url):
        prefix = "sqlite:///"
        if not url.startswith(prefix) or not url[len(prefix):]:
            raise ValueError("DATABASE_URL must name a SQLite file")
        self.path = Path(url[len(prefix):])

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.path)
        try:
            database.row_factory = sqlite3.Row
            yield database
            database.commit()
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as database:
            database.executescript("""
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_by_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    system_prompt TEXT NOT NULL
                );
            """)

    def check(self):
        with self.connect() as database:
            database.execute("SELECT id FROM messages LIMIT 1").fetchone()

    def read(self, session_id):
        with self.connect() as database:
            database.execute("BEGIN")
            session = database.execute(
                "SELECT system_prompt FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            rows = database.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

        messages = []
        for row in rows:
            message = {"role": row["role"], "content": row["content"]}
            if row["metadata"] is not None:
                message["metadata"] = json.loads(row["metadata"])
            messages.append(message)
        return (session["system_prompt"] if session is not None else None), messages

    def append(self, session_id, prompt, message, reply, metadata):
        timestamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as database:
            database.execute(
                """INSERT INTO sessions (id, system_prompt) VALUES (?, ?)
                   ON CONFLICT(id) DO UPDATE SET system_prompt = excluded.system_prompt""",
                (session_id, prompt),
            )
            database.executemany(
                """INSERT INTO messages (session_id, role, content, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (session_id, "user", message, None, timestamp),
                    (session_id, "assistant", reply, json.dumps(metadata), timestamp),
                ],
            )
