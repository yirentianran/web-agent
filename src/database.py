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
    user_id       TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'user',
    status        TEXT NOT NULL DEFAULT 'active',
    disabled_at   REAL,
    disabled_by   TEXT,
    created_at    REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_active_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    title        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'idle',
    cost_usd     REAL NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_active_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created ON sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
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
    user_id TEXT PRIMARY KEY REFERENCES users(user_id),
    preferences TEXT NOT NULL DEFAULT '{}',
    entity_memory TEXT NOT NULL DEFAULT '{}',
    audit_context TEXT NOT NULL DEFAULT '{}',
    file_memory TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- Tasks (sub-agent)
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    subject TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active_form TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    blocked_by TEXT NOT NULL DEFAULT '[]',
    parent_task_id TEXT REFERENCES tasks(id),
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    completed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks(user_id, created_at DESC);

-- MCP servers
CREATE TABLE IF NOT EXISTS mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'stdio',
    command TEXT,
    args TEXT NOT NULL DEFAULT '[]',
    url TEXT,
    env TEXT NOT NULL DEFAULT '{}',
    tools TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    access TEXT NOT NULL DEFAULT 'all',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- Skill feedback
CREATE TABLE IF NOT EXISTS skill_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    session_id TEXT,
    rating INTEGER NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    user_edits TEXT NOT NULL DEFAULT '',
    skill_version TEXT NOT NULL DEFAULT '',
    conversation_snippet TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_feedback_skill ON skill_feedback(skill_name);

-- Audit log (SQL-based)
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    category   TEXT NOT NULL,
    user_id    TEXT,
    action     TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_log_category ON audit_log(category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id, created_at DESC);

-- Uploads
CREATE TABLE IF NOT EXISTS uploads (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    filename    TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_size   INTEGER NOT NULL DEFAULT 0,
    mime_type   TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_uploads_user ON uploads(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uploads_session ON uploads(session_id);

-- Generated files
CREATE TABLE IF NOT EXISTS generated_files (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    filename    TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_size   INTEGER NOT NULL DEFAULT 0,
    mime_type   TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_generated_files_user ON generated_files(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generated_files_session ON generated_files(session_id);
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
        # Set busy timeout to handle write contention gracefully (30s)
        await self._pool.execute("PRAGMA busy_timeout=30000")
        # Use NORMAL synchronous for better write performance in WAL mode
        await self._pool.execute("PRAGMA synchronous=NORMAL")

        # Create all tables
        await self._pool.executescript(_CREATE_TABLES)
        await self._pool.commit()

        # Add user_edits column if it doesn't exist (migration for existing DBs)
        try:
            await self._pool.execute(
                "ALTER TABLE skill_feedback ADD COLUMN user_edits TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass  # Column already exists

        # Add conversation_snippet column if it doesn't exist (migration for existing DBs)
        try:
            await self._pool.execute(
                "ALTER TABLE skill_feedback ADD COLUMN conversation_snippet TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass  # Column already exists

        self._initialized = True

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection. Raises RuntimeError if not initialized."""
        if self._pool is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        yield self._pool

    async def migrate_v2(self) -> None:
        """Migrate existing databases from v1 to v2 schema.

        Safe to run on already-migrated databases (all ALTER statements
        are wrapped in try/except).
        """
        async with self.connection() as conn:
            # Phase 1: Rename users.id -> users.user_id
            try:
                await conn.execute("ALTER TABLE users RENAME COLUMN id TO user_id")
            except Exception:
                pass  # Already renamed

            # Add new columns to users
            for col_stmt in [
                "ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'",
                "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
                "ALTER TABLE users ADD COLUMN disabled_at REAL",
                "ALTER TABLE users ADD COLUMN disabled_by TEXT",
            ]:
                try:
                    await conn.execute(col_stmt)
                except Exception:
                    pass  # Column already exists

            # Phase 2: Rename sessions.id -> sessions.session_id
            try:
                await conn.execute(
                    "ALTER TABLE sessions RENAME COLUMN id TO session_id"
                )
            except Exception:
                pass

            # Phase 3: Add new tables (CREATE TABLE IF NOT EXISTS is idempotent)
            await conn.executescript(
                """CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    user_id TEXT,
                    action TEXT,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                );
                CREATE INDEX IF NOT EXISTS idx_audit_log_category
                    ON audit_log(category, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_log_user
                    ON audit_log(user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    filename TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    mime_type TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                );
                CREATE INDEX IF NOT EXISTS idx_uploads_user
                    ON uploads(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_uploads_session ON uploads(session_id);
                CREATE TABLE IF NOT EXISTS generated_files (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    filename TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    mime_type TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                );
                CREATE INDEX IF NOT EXISTS idx_generated_files_user
                    ON generated_files(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_generated_files_session
                    ON generated_files(session_id);"""
            )
            await conn.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._initialized = False
