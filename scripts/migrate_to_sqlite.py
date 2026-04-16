#!/usr/bin/env python3
"""Migrate file-based data to SQLite database.

Reads existing JSONL session files, user memory, and metadata from the
data/ directory and imports them into a SQLite database.

Usage:
    python scripts/migrate_to_sqlite.py
    python scripts/migrate_to_sqlite.py --db-path /custom/path/web-agent.db
    python scripts/migrate_to_sqlite.py --data-root /custom/data --dry-run

This script is read-only until --apply is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate file data to SQLite")
    parser.add_argument("--db-path", default="data/web-agent.db", help="Path to SQLite database")
    parser.add_argument("--data-root", default="data", help="Root data directory")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without writing")
    parser.add_argument("--apply", action="store_true", help="Actually perform the migration")
    return parser.parse_args()


def count_lines(path: Path) -> int:
    """Count non-empty lines in a file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except (OSError, UnicodeDecodeError):
        return 0


def read_jsonl(path: Path) -> list[dict]:
    """Read all JSON objects from a JSONL file."""
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except (OSError, UnicodeDecodeError):
        pass
    return results


def extract_user_id_from_session_id(session_id: str) -> str:
    """Parse user_id from session_id format: session_{user_id}_{timestamp}_{uuid}."""
    parts = session_id.split("_")
    if len(parts) >= 3:
        return "_".join(parts[1:-2])
    return "default"


def main() -> None:
    args = parse_args()
    data_root = Path(args.data_root)
    db_path = Path(args.db_path)

    if not data_root.exists():
        print(f"ERROR: Data root {data_root} does not exist")
        sys.exit(1)

    msg_buffer_dir = data_root / ".msg-buffer"
    users_dir = data_root / "users"

    # ── Scan Phase ──────────────────────────────────────────────────

    print(f"Scanning data directory: {data_root}")

    # 1. Count session files
    session_files = sorted(msg_buffer_dir.glob("*.jsonl")) if msg_buffer_dir.exists() else []
    total_sessions = len(session_files)
    total_messages = 0
    for sf in session_files:
        total_messages += count_lines(sf)

    print(f"  Sessions: {total_sessions}")
    print(f"  Messages: {total_messages}")

    # 2. Count users
    user_dirs = sorted(users_dir.iterdir()) if users_dir.exists() and users_dir.is_dir() else []
    user_ids = [d.name for d in user_dirs if d.is_dir()]
    print(f"  Users: {len(user_ids)}")

    # 3. Count user memory files
    memory_files = []
    for uid in user_ids:
        mf = users_dir / uid / "memory.json"
        if mf.exists():
            memory_files.append(mf)
    print(f"  User memory files: {len(memory_files)}")

    # 4. Count session meta files
    meta_files = []
    for uid in user_ids:
        sessions_meta_dir = users_dir / uid / "claude-data" / "sessions"
        if sessions_meta_dir.exists():
            meta_files.extend(sessions_meta_dir.glob("*.meta.json"))
    print(f"  Session meta files: {len(meta_files)}")

    if args.dry_run or not args.apply:
        print("\nDry run. Pass --apply to perform the migration.")
        return

    # ── Migration Phase ─────────────────────────────────────────────

    import sqlite3

    print(f"\nMigrating to {db_path}...")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    # Create schema (same as src/database.py)
    from src.database import _CREATE_TABLES
    conn.executescript(_CREATE_TABLES)
    conn.commit()

    # 1. Import users
    print("\n1. Importing users...")
    user_count = 0
    for uid in user_ids:
        conn.execute("INSERT OR IGNORE INTO users (id, last_active_at) VALUES (?, ?)", (uid, time.time()))
        user_count += 1
    conn.commit()
    print(f"   {user_count} users inserted")

    # 2. Import sessions and messages
    print("\n2. Importing sessions and messages...")
    session_imported = 0
    message_imported = 0

    # Build a map of session_id -> meta info
    meta_map: dict[str, dict] = {}
    for mf in meta_files:
        try:
            meta = json.loads(mf.read_text())
            sid = mf.stem.replace(".meta", "")
            meta_map[sid] = meta
        except (json.JSONDecodeError, OSError):
            pass

    for sf in session_files:
        session_id = sf.stem
        user_id = extract_user_id_from_session_id(session_id)

        # Ensure user exists
        conn.execute("INSERT OR IGNORE INTO users (id, last_active_at) VALUES (?, ?)", (user_id, time.time()))

        # Get session metadata
        title = meta_map.get(session_id, {}).get("title", "")
        stat = sf.stat()

        # Read messages first so we know the count
        messages = read_jsonl(sf)

        # Insert session with correct message_count
        conn.execute(
            """INSERT OR REPLACE INTO sessions
               (id, user_id, title, status, cost_usd, message_count, created_at, last_active_at)
               VALUES (?, ?, ?, 'idle', 0, ?, ?, ?)""",
            (session_id, user_id, title, len(messages), stat.st_mtime, stat.st_mtime),
        )
        if messages:
            # Batch insert messages
            rows = []
            for seq, msg in enumerate(messages):
                usage_json = json.dumps(msg["usage"], ensure_ascii=False) if msg.get("usage") else None
                payload_json = json.dumps(msg, ensure_ascii=False)
                rows.append((
                    session_id, seq,
                    msg.get("type", ""),
                    msg.get("subtype"),
                    msg.get("name"),
                    msg.get("content"),
                    payload_json,
                    usage_json,
                    stat.st_mtime,
                ))
            conn.executemany(
                """INSERT INTO messages
                   (session_id, seq, type, subtype, name, content, payload, usage, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            message_imported += len(messages)

        session_imported += 1
        if session_imported % 50 == 0:
            conn.commit()
            print(f"   ... {session_imported} sessions, {message_imported} messages")

    conn.commit()
    print(f"   {session_imported} sessions, {message_imported} messages imported")

    # 3. Import user memory
    print("\n3. Importing user memory...")
    memory_imported = 0
    for mf in memory_files:
        uid = mf.parent.name
        try:
            data = json.loads(mf.read_text())
            conn.execute(
                """INSERT OR REPLACE INTO user_memory
                   (user_id, preferences, entity_memory, audit_context, file_memory, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    uid,
                    json.dumps(data.get("preferences", {})),
                    json.dumps(data.get("entity_memory", {})),
                    json.dumps(data.get("audit_context", {})),
                    json.dumps(data.get("file_memory", [])),
                    data.get("updated_at", time.time()),
                ),
            )
            memory_imported += 1
        except (json.JSONDecodeError, OSError):
            pass
    conn.commit()
    print(f"   {memory_imported} user memories imported")

    # ── Verification ────────────────────────────────────────────────

    print("\n4. Verifying migration...")
    cursor = conn.execute("SELECT COUNT(*) FROM users")
    db_users = cursor.fetchone()[0]
    cursor = conn.execute("SELECT COUNT(*) FROM sessions")
    db_sessions = cursor.fetchone()[0]
    cursor = conn.execute("SELECT COUNT(*) FROM messages")
    db_messages = cursor.fetchone()[0]

    print(f"   DB users: {db_users}")
    print(f"   DB sessions: {db_sessions}")
    print(f"   DB messages: {db_messages}")

    # Verify counts match
    assert db_sessions == total_sessions, (
        f"Session count mismatch: {db_sessions} != {total_sessions}"
    )
    assert db_messages == total_messages, (
        f"Message count mismatch: {db_messages} != {total_messages}"
    )

    conn.close()
    print("\nMigration complete!")
    print(f"  Database: {db_path}")
    print(f"  Size: {db_path.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
