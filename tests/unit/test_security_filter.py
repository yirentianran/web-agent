"""Tests for security filter classes."""

import time

from src.security_filter import BashCommandFilter, FileAccessFilter, OutputFilter


class TestOutputFilter:
    def test_hides_api_key_pattern(self):
        text = "My key is sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = OutputFilter.scan(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result
        assert "*** (hidden) ***" in result

    def test_hides_env_assignment(self):
        text = "DATABASE_URL=postgres://secret:pass@host/db"
        result = OutputFilter.scan(text)
        assert "postgres://secret:pass@host/db" not in result or "*** (hidden) ***" in result

    def test_hides_internal_paths(self):
        text = "The file is at /Users/mac/Documents/Projects/web-agent/src/main.py"
        result = OutputFilter.scan(text)
        assert "/Users/mac/Documents/Projects/web-agent" not in result
        assert "*** (hidden) ***" in result

    def test_hides_port_info(self):
        text = "Running on port: 8000"
        result = OutputFilter.scan(text)
        assert "8000" not in result

    def test_hides_port_natural_language(self):
        """Port in natural language like 'on port 3000' should be hidden."""
        text = "Server started on port 3000"
        result = OutputFilter.scan(text)
        assert "3000" not in result
        assert "*** (hidden) ***" in result

    def test_blocks_uname_all_variants(self):
        """Any uname invocation line should be blocked."""
        text = "System: uname -r reports 5.15.0-generic"
        result = OutputFilter.scan(text)
        assert "[Content blocked]" in result
        assert "uname -r" not in result

    def test_blocks_all_proc_paths(self):
        """Any /proc/ path should be blocked, not just cpuinfo/meminfo."""
        text = "Here is /proc/version info"
        result = OutputFilter.scan(text)
        assert "[Content blocked]" in result
        assert "/proc/version" not in result

    def test_blocks_uname_output(self):
        text = "uname -a: Linux hostname 5.15.0-generic #1 SMP x86_64"
        result = OutputFilter.scan(text)
        assert "[Content blocked]" in result
        assert "uname -a" not in result
        assert "Linux hostname" not in result

    def test_blocks_proc_output(self):
        text = "cat /proc/cpuinfo\nprocessor\t: 0\nmodel name\t: Intel"
        result = OutputFilter.scan(text)
        assert "[Content blocked]" in result

    def test_safe_text_passes_unchanged(self):
        text = "Hello, I've created the file for you."
        result = OutputFilter.scan(text)
        assert result == text

    def test_empty_string(self):
        assert OutputFilter.scan("") == ""

    def test_multiple_secrets_in_one_text(self):
        text = "Key: sk-aaaabbbbccccddddeeeeffffgggghhhh and port: 3000"
        result = OutputFilter.scan(text)
        assert "sk-aaaabbbbccccddddeeeeffffgggghhhh" not in result
        assert "3000" not in result
        assert result.count("*** (hidden) ***") >= 2

    def test_performance(self):
        text = "Normal text without any secrets. " * 100
        start = time.perf_counter()
        for _ in range(100):
            OutputFilter.scan(text)
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed / 100) * 1000
        assert avg_ms < 1.0, f"Average scan time {avg_ms:.3f}ms exceeds 1ms"


class TestBashCommandFilter:
    def test_blocks_env_command(self):
        allowed, reason = BashCommandFilter.check("env")
        assert not allowed

    def test_blocks_printenv(self):
        allowed, reason = BashCommandFilter.check("printenv")
        assert not allowed

    def test_blocks_uname(self):
        allowed, reason = BashCommandFilter.check("uname -a")
        assert not allowed

    def test_blocks_docker_ps(self):
        allowed, reason = BashCommandFilter.check("docker ps")
        assert not allowed

    def test_blocks_cat_proc(self):
        allowed, reason = BashCommandFilter.check("cat /proc/cpuinfo")
        assert not allowed

    def test_blocks_cat_env_file(self):
        allowed, reason = BashCommandFilter.check("cat .env")
        assert not allowed

    def test_blocks_cat_etc_passwd(self):
        allowed, reason = BashCommandFilter.check("cat /etc/passwd")
        assert not allowed

    def test_blocks_export_p(self):
        allowed, reason = BashCommandFilter.check("export -p")
        assert not allowed

    def test_allows_safe_command(self):
        allowed, reason = BashCommandFilter.check("ls -la")
        assert allowed

    def test_allows_python_script(self):
        allowed, reason = BashCommandFilter.check("python3 script.py")
        assert allowed

    def test_blocks_command_in_pipe(self):
        allowed, reason = BashCommandFilter.check("echo test | env | head")
        assert not allowed

    def test_blocks_sudo_env(self):
        allowed, reason = BashCommandFilter.check("sudo env")
        assert not allowed


class TestFileAccessFilter:
    def test_blocks_env_file(self):
        allowed, reason = FileAccessFilter.check(".env")
        assert not allowed

    def test_blocks_env_local(self):
        allowed, reason = FileAccessFilter.check(".env.local")
        assert not allowed

    def test_blocks_claude_dir(self):
        allowed, reason = FileAccessFilter.check(".claude/settings.json")
        assert not allowed

    def test_blocks_claude_md(self):
        allowed, reason = FileAccessFilter.check("CLAUDE.md")
        assert not allowed

    def test_blocks_dockerfile(self):
        allowed, reason = FileAccessFilter.check("Dockerfile")
        assert not allowed

    def test_blocks_git_config(self):
        allowed, reason = FileAccessFilter.check(".git/config")
        assert not allowed

    def test_blocks_pem_file(self):
        allowed, reason = FileAccessFilter.check("cert.pem")
        assert not allowed

    def test_allows_regular_file(self):
        allowed, reason = FileAccessFilter.check("outputs/report.txt")
        assert allowed

    def test_allows_python_file(self):
        allowed, reason = FileAccessFilter.check("src/main.py")
        assert allowed
