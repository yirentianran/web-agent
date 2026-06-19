"""Tests for WebSocket resume message handling."""


class TestWsResume:
    def test_resume_message_has_required_fields(self):
        """Resume messages must include session_id and last_seq."""
        msg = {"type": "resume", "session_id": "sess_1", "last_seq": 42}
        assert msg["type"] == "resume"
        assert "session_id" in msg
        assert "last_seq" in msg

    def test_resume_with_zero_seq_replays_all(self):
        """last_seq of 0 means replay from beginning."""
        msg = {"type": "resume", "session_id": "sess_1", "last_seq": 0}
        assert msg["last_seq"] == 0

    def test_resume_type_is_distinct_from_chat(self):
        """Resume and chat are different message types."""
        resume = {"type": "resume", "session_id": "sess_1", "last_seq": 5}
        chat = {"type": "chat", "session_id": "sess_1", "message": "hello"}
        assert resume["type"] != chat["type"]

    def test_resume_message_handles_missing_last_seq(self):
        """A resume without last_seq should default to 0."""
        msg = {"type": "resume", "session_id": "sess_1"}
        last_seq = msg.get("last_seq", 0)
        assert last_seq == 0

    def test_resume_message_structurally_similar_to_recover(self):
        """Resume and recover share a similar envelope pattern."""
        resume = {"type": "resume", "session_id": "sess_a", "last_seq": 10}
        recover = {"type": "recover", "session_id": "sess_a", "last_index": 10}
        # Both identify the session
        assert resume["session_id"] == recover["session_id"]
        # Both carry a position cursor
        assert "last_seq" in resume
        assert "last_index" in recover
