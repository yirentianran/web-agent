"""Tests for L1/L2 memory manager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.memory import MemoryManager, _deep_merge


class TestDeepMerge:
    def test_simple_merge(self) -> None:
        base = {"a": 1, "b": 2}
        patch = {"b": 3, "c": 4}
        result = _deep_merge(base, patch)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_dict_merge(self) -> None:
        base = {"prefs": {"theme": "light", "lang": "en"}}
        patch = {"prefs": {"theme": "dark"}}
        result = _deep_merge(base, patch)
        assert result["prefs"]["theme"] == "dark"
        assert result["prefs"]["lang"] == "en"

    def test_list_extend(self) -> None:
        base = {"files": ["a.py", "b.py"]}
        patch = {"files": ["c.py"]}
        result = _deep_merge(base, patch)
        assert result["files"] == ["a.py", "b.py", "c.py"]

    def test_empty_base(self) -> None:
        result = _deep_merge({}, {"a": 1})
        assert result == {"a": 1}


class TestMemoryManager:
    def test_read_empty_returns_default(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        data = mgr.read()
        assert data["user_id"] == "alice"

    def test_update_and_read(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr.update({"preferences": {"theme": "dark"}})
        data = mgr.read()
        assert data["preferences"]["theme"] == "dark"

    def test_deep_merge_on_update(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr.update({"entity_memory": {"name": "Acme"}})
        mgr.update({"entity_memory": {"industry": "Tech"}})
        data = mgr.read()
        assert data["entity_memory"]["name"] == "Acme"
        assert data["entity_memory"]["industry"] == "Tech"

    def test_replace(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr.update({"old": "data"})
        mgr.replace({"new": "data"})
        data = mgr.read()
        assert "old" not in data
        assert data["new"] == "data"

    def test_agent_notes_crud(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr.write_agent_note("findings.md", "## Critical\nNothing found.")
        assert mgr.read_agent_note("findings.md") == "## Critical\nNothing found."
        assert mgr.read_agent_note("missing.md") == ""

    def test_list_agent_notes(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr.write_agent_note("notes.md", "some notes")
        mgr.write_agent_note("plan.md", "the plan")
        notes = mgr.list_agent_notes()
        assert len(notes) == 2
        assert {n["filename"] for n in notes} == {"notes.md", "plan.md"}

    def test_delete_agent_note(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr.write_agent_note("temp.md", "temp content")
        mgr.delete_agent_note("temp.md")
        assert mgr.read_agent_note("temp.md") == ""

    def test_load_agent_memory_for_prompt(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr.write_agent_note("findings.md", "Found X")
        mgr.write_agent_note("notes.md", "Note Y")
        prompt = mgr.load_agent_memory_for_prompt()
        assert "## Agent Memory" in prompt
        assert "### findings.md" in prompt
        assert "Found X" in prompt

    def test_load_empty_agent_memory(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        assert mgr.load_agent_memory_for_prompt() == ""

    def test_corrupted_memory_file(self, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path)
        mgr._memory_file.write_text("not valid json{{{")
        data = mgr.read()
        assert data == {"user_id": "alice"}
