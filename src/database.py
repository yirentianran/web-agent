"""SQLite database connection management and schema initialization.

Uses a single aiosqlite connection with WAL mode for crash safety.
All operations are serialized through one background thread, eliminating
write-write lock contention. WAL auto-checkpoint is disabled and replaced
with a periodic PASSIVE checkpoint that never blocks readers or writers.

Usage:
    from src.database import Database

    db = Database(db_path=Path("data/web-agent.db"))
    await db.init()
    async with db.connection() as conn:
        await conn.execute("SELECT 1")
    await db.close()
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

_CHECKPOINT_INTERVAL = 300  # seconds between WAL checkpoints


def connect_sync(db_path: Path) -> sqlite3.Connection:
    """Open a sync sqlite3 connection with busy_timeout set.

    Use this for any sync code that needs direct DB access alongside
    the aiosqlite connection. Without busy_timeout, a sync connection
    fails immediately with "database is locked" whenever the aiosqlite
    connection holds a write lock.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


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
    last_active_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    deleted_at   REAL
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
    session_id  TEXT NOT NULL,
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
    session_id  TEXT NOT NULL,
    filename    TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_size   INTEGER NOT NULL DEFAULT 0,
    mime_type   TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_generated_files_user ON generated_files(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generated_files_session ON generated_files(session_id);
CREATE INDEX IF NOT EXISTS idx_generated_files_user_stored_name ON generated_files(user_id, stored_name);

-- Skills registry
CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'personal',
    owner_id    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'active',
    version     TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE(skill_name, source)
);

CREATE INDEX IF NOT EXISTS idx_skills_source ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);

-- Skill usage tracking
CREATE TABLE IF NOT EXISTS skill_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL REFERENCES skills(skill_name),
    user_id        TEXT NOT NULL DEFAULT '',
    session_id     TEXT NOT NULL DEFAULT '',
    version_number INTEGER NOT NULL DEFAULT 0,
    action         TEXT NOT NULL DEFAULT 'use',
    created_at     REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_skill ON skill_usage(skill_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_user ON skill_usage(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_session ON skill_usage(session_id);

-- Skill version metadata
CREATE TABLE IF NOT EXISTS skill_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL REFERENCES skills(skill_name),
    version_number INTEGER NOT NULL,
    path           TEXT NOT NULL DEFAULT '',
    change_summary TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',
    created_by     TEXT NOT NULL DEFAULT 'user',
    file_count     INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_versions_skill ON skill_versions(skill_name, version_number DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_versions_unique ON skill_versions(skill_name, version_number);
"""


class Database:
    """SQLite database with single aiosqlite connection.

    A single connection serializes all operations through one background
    thread, eliminating write-write lock contention. WAL auto-checkpoint
    is disabled — it was the root cause of random "database is locked"
    errors — and replaced with a periodic PASSIVE checkpoint.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._checkpoint_task: asyncio.Task | None = None
        self._initialized = False

    async def init(self) -> None:
        """Open connection, apply PRAGMAs, create tables, start checkpoint loop."""
        if self._initialized:
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row

        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=30000")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        # wal_autocheckpoint=0 disables auto-checkpoint entirely.
        # Auto-checkpoints were the root cause of random "database is locked"
        # errors — each checkpoint briefly holds an exclusive lock, and under
        # write load these collide with INSERT/UPDATE operations.
        # A manual PASSIVE checkpoint runs on a timer instead; PASSIVE never
        # blocks readers or writers.
        await self._conn.execute("PRAGMA wal_autocheckpoint=0")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        await self._conn.executescript(_CREATE_TABLES)
        await self._conn.commit()

        # Add user_edits column if it doesn't exist (migration for existing DBs)
        try:
            await self._conn.execute(
                "ALTER TABLE skill_feedback ADD COLUMN user_edits TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass

        # Add conversation_snippet column if it doesn't exist
        try:
            await self._conn.execute(
                "ALTER TABLE skill_feedback ADD COLUMN conversation_snippet TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass

        # Remove FK constraints on uploads and generated_files session_id columns
        # by recreating tables without the constraints (SQLite limitation).
        try:
            await self._migrate_drop_session_fks()
        except Exception:
            pass

        # Add deleted_at column for soft-deleted sessions
        try:
            await self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN deleted_at REAL"
            )
        except Exception:
            pass
        # Create partial index for non-deleted sessions (requires deleted_at column)
        try:
            await self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_not_deleted "
                "ON sessions(user_id, created_at DESC) WHERE deleted_at IS NULL"
            )
        except Exception:
            pass

        # Add collective intelligence tables
        try:
            await self.migrate_collective_intelligence()
        except Exception:
            pass

        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())
        self._initialized = True

    async def _checkpoint_loop(self) -> None:
        """Periodically run a PASSIVE WAL checkpoint.

        PASSIVE mode: if another reader/writer is active, the checkpoint
        returns immediately without blocking. This keeps the WAL file from
        growing unboundedly (wal_autocheckpoint=0 disables auto-checkpoint).
        """
        while True:
            await asyncio.sleep(_CHECKPOINT_INTERVAL)
            if self._conn is not None:
                try:
                    await self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    pass

    async def _migrate_drop_session_fks(self) -> None:
        """Drop FK constraints on uploads/generated_files session_id columns.

        SQLite cannot DROP CONSTRAINT directly, so we recreate the tables
        without the FK references while preserving all existing data.
        """
        # Check if migration is needed by inspecting table SQL
        row = await self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='uploads'"
        ).fetchone()
        if row and "REFERENCES sessions" not in row[0]:
            return  # Already migrated

        await self._conn.execute("PRAGMA foreign_keys=OFF")
        await self._conn.execute("BEGIN TRANSACTION")

        # Recreate uploads
        await self._conn.execute("ALTER TABLE uploads RENAME TO uploads_old")
        await self._conn.execute(
            "CREATE TABLE uploads ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), "
            "session_id TEXT NOT NULL, filename TEXT NOT NULL, stored_name TEXT NOT NULL, "
            "file_size INTEGER NOT NULL DEFAULT 0, mime_type TEXT NOT NULL DEFAULT '', "
            "url TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))"
            ")"
        )
        await self._conn.execute(
            "INSERT INTO uploads SELECT id, user_id, session_id, filename, stored_name, "
            "file_size, mime_type, url, created_at FROM uploads_old"
        )
        await self._conn.execute("DROP TABLE uploads_old")

        # Recreate generated_files
        await self._conn.execute(
            "ALTER TABLE generated_files RENAME TO generated_files_old"
        )
        await self._conn.execute(
            "CREATE TABLE generated_files ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), "
            "session_id TEXT NOT NULL, filename TEXT NOT NULL, stored_name TEXT NOT NULL, "
            "file_size INTEGER NOT NULL DEFAULT 0, mime_type TEXT NOT NULL DEFAULT '', "
            "url TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))"
            ")"
        )
        await self._conn.execute(
            "INSERT INTO generated_files SELECT id, user_id, session_id, filename, "
            "stored_name, file_size, mime_type, url, created_at FROM generated_files_old"
        )
        await self._conn.execute("DROP TABLE generated_files_old")

        # Recreate indexes
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_uploads_user ON uploads(user_id, created_at DESC)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_uploads_session ON uploads(session_id)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_generated_files_user ON generated_files(user_id, created_at DESC)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_generated_files_session ON generated_files(session_id)"
        )

        await self._conn.execute("COMMIT")
        await self._conn.execute("PRAGMA foreign_keys=ON")

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield the database connection. Raises RuntimeError if not initialized."""
        if not self._initialized or self._conn is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        yield self._conn

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
                    session_id TEXT NOT NULL,
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
                    session_id TEXT NOT NULL,
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
                    ON generated_files(session_id);
                CREATE INDEX IF NOT EXISTS idx_generated_files_user_stored_name
                    ON generated_files(user_id, stored_name);"""
            )
            await conn.commit()

            # Phase 4: Migrate skills table from UNIQUE(skill_name) to UNIQUE(skill_name, source).
            # Check if migration is needed by looking at the index SQL.
            try:
                index_rows = await conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='skills'"
                )
                row = await index_rows.fetchone()
                needs_migration = row and "UNIQUE(skill_name, source)" not in row[0]
                if needs_migration:
                    await conn.execute("ALTER TABLE skills RENAME TO skills_old")
                    await conn.execute(
                        """CREATE TABLE skills (
                            id          INTEGER PRIMARY KEY AUTOINCREMENT,
                            skill_name  TEXT NOT NULL,
                            source      TEXT NOT NULL DEFAULT 'personal',
                            owner_id    TEXT NOT NULL DEFAULT '',
                            description TEXT NOT NULL DEFAULT '',
                            category    TEXT NOT NULL DEFAULT '',
                            tags        TEXT NOT NULL DEFAULT '[]',
                            status      TEXT NOT NULL DEFAULT 'active',
                            version     TEXT NOT NULL DEFAULT '',
                            path        TEXT NOT NULL DEFAULT '',
                            created_at  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                            updated_at  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                            UNIQUE(skill_name, source)
                        )"""
                    )
                    await conn.execute(
                        """INSERT INTO skills (skill_name, source, owner_id, description, category, tags, status, version, path, created_at, updated_at)
                           SELECT skill_name, source, owner_id, description, category, tags, status, version, path, created_at, updated_at
                           FROM skills_old"""
                    )
                    await conn.execute("DROP TABLE skills_old")
            except Exception:
                pass  # Already migrated

    async def migrate_collective_intelligence(self) -> None:
        """Add collective intelligence tables and FTS5 indexes.

        Phase 1: Only FTS5 full-text search. No sqlite-vec embeddings.
        Safe to run on already-migrated databases.
        """
        async with self.connection() as conn:
            # 1. Wiki pages table
            await conn.execute("""CREATE TABLE IF NOT EXISTS wiki_pages (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                category TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                source TEXT NOT NULL DEFAULT 'auto-generated',
                confidence REAL NOT NULL DEFAULT 0.5,
                validation_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )""")

            # 2. Session summaries
            await conn.execute("""CREATE TABLE IF NOT EXISTS session_summaries (
                session_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )""")

            # 3. FTS5 full-text indexes
            await conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts
                USING fts5(title, body, tags, content='wiki_pages')""")

            await conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS session_summary_fts
                USING fts5(summary, content='session_summaries')""")

            # 4. Skill promotion queue
            await conn.execute("""CREATE TABLE IF NOT EXISTS skill_promotion_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                original_owner_id TEXT NOT NULL,
                uses_count INTEGER NOT NULL,
                unique_users_count INTEGER NOT NULL,
                avg_rating REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                admin_review_comment TEXT,
                reviewed_at REAL,
                reviewed_by TEXT,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )""")

            # 5. Learned patterns
            await conn.execute("""CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                pattern_data TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )""")

            await conn.commit()

    async def close(self) -> None:
        """Close the connection and stop the checkpoint loop."""
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            self._checkpoint_task = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._initialized = False
