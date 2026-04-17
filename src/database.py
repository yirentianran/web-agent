"""SQLite database connection management and schema initialization.

Provides an async-compatible database layer using aiosqlite with WAL mode
for concurrent read support and write serialization.

Usage:
    from src.database import Database

    db = Database(db_path=Path("data/web-agent.db"))
    await db.init()
    async with db.connection() as conn:
        await conn.execute("SELECT 1")
    await db.close()
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


_CREATE_TABLES = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_active_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    cost_usd REAL NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_active_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created ON sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    subtype TEXT,
    name TEXT,
    content TEXT,
    payload TEXT,
    usage TEXT,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);

-- User memory (L1 platform memory)
CREATE TABLE IF NOT EXISTS user_memory (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    preferences TEXT NOT NULL DEFAULT '{}',
    entity_memory TEXT NOT NULL DEFAULT '{}',
    audit_context TEXT NOT NULL DEFAULT '{}',
    file_memory TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- Tasks (sub-agent)
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    subject TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active_form TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    blocked_by TEXT NOT NULL DEFAULT '[]',
    parent_task_id TEXT REFERENCES tasks(id),
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks(user_id, created_at DESC);

-- Skill feedback
CREATE TABLE IF NOT EXISTS skill_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    session_id TEXT,
    rating INTEGER NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    skill_version TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_feedback_skill ON skill_feedback(skill_name);
"""


class Database:
    """SQLite database manager with async connection pooling via aiosqlite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._pool: aiosqlite.Connection | None = None
        self._initialized = False

    async def init(self) -> None:
        """Initialize the database connection and create tables."""
        if self._initialized:
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._pool = await aiosqlite.connect(str(self.db_path))
        self._pool.row_factory = aiosqlite.Row

        # Enable WAL mode for concurrent reads
        await self._pool.execute("PRAGMA journal_mode=WAL")
        # Set busy timeout to handle write contention gracefully
        await self._pool.execute("PRAGMA busy_timeout=5000")

        # Create all tables
        await self._pool.executescript(_CREATE_TABLES)
        await self._pool.commit()

        self._initialized = True

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection. Raises RuntimeError if not initialized."""
        if self._pool is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        yield self._pool

    async def close(self) -> None:
        """Close the database connection."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._initialized = False
