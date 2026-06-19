"""Tests for security filter classes."""

import time

from src.security.filters import BashCommandFilter, FileAccessFilter, OutputFilter


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

    def test_hides_env_dump_model(self):
        """MODEL=deepseek-v4-pro from env dump should be hidden."""
        text = "MODEL=deepseek-v4-pro"
        result = OutputFilter.scan(text)
        assert "deepseek-v4-pro" not in result
        assert "[Content blocked]" in result

    def test_hides_env_dump_container_mode(self):
        """CONTAINER_MODE=false should be hidden."""
        text = "CONTAINER_MODE=false"
        result = OutputFilter.scan(text)
        assert "false" not in result
        assert "[Content blocked]" in result

    def test_hides_env_dump_max_turns(self):
        """MAX_TURNS=500 should be hidden."""
        text = "MAX_TURNS=500"
        result = OutputFilter.scan(text)
        assert "500" not in result
        assert "[Content blocked]" in result

    def test_hides_env_dump_data_root(self):
        """DATA_ROOT=./data should be hidden."""
        text = "DATA_ROOT=./data"
        result = OutputFilter.scan(text)
        assert "./data" not in result
        assert "[Content blocked]" in result

    def test_hides_env_dump_jwt_secret(self):
        """JWT_SECRET value should be hidden."""
        text = "JWT_SECRET=web-agent-dev-secret-2026"
        result = OutputFilter.scan(text)
        assert "web-agent-dev-secret-2026" not in result
        assert "[Content blocked]" in result

    def test_hides_env_dump_log_level(self):
        """LOG_LEVEL=DEBUG should be hidden."""
        text = "LOG_LEVEL=DEBUG"
        result = OutputFilter.scan(text)
        assert "DEBUG" not in result
        assert "[Content blocked]" in result

    def test_hides_env_dump_enforce_auth(self):
        """ENFORCE_AUTH=true should be hidden."""
        text = "ENFORCE_AUTH=true"
        result = OutputFilter.scan(text)
        assert "true" not in result
        assert "[Content blocked]" in result

    def test_hides_full_env_dump_block(self):
        """Multi-line env dump should have all lines completely blocked."""
        text = (
            "HOME=/home/agent\n"
            "MODEL=deepseek-v4-pro\n"
            "LOG_LEVEL=DEBUG\n"
            "CONTAINER_MODE=false\n"
            "DATA_ROOT=./data\n"
            "MAX_TURNS=500\n"
            "JWT_SECRET=web-agent-dev-secret"
        )
        result = OutputFilter.scan(text)
        # Values should all be hidden
        assert "/home/agent" not in result
        assert "deepseek-v4-pro" not in result
        assert "web-agent-dev-secret" not in result
        assert "500" not in result
        # Variable names should NOT be visible either
        assert "HOME=" not in result
        assert "MODEL=" not in result
        assert "JWT_SECRET=" not in result
        # Each env var line becomes [Content blocked]
        assert "[Content blocked]" in result

    def test_env_var_mixed_with_safe_text(self):
        """Env vars should be blocked, safe text should remain."""
        text = "Here are the variables:\nMODEL=deepseek-v4-pro\nEnd of dump.\n"
        result = OutputFilter.scan(text)
        assert "MODEL=deepseek-v4-pro" not in result
        assert "Here are the variables:" in result
        assert "End of dump." in result
        assert "[Content blocked]" in result


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

    def test_blocks_python_c(self):
        """python3 -c with inline code should be blocked."""
        allowed, reason = BashCommandFilter.check('python3 -c "import os; print(os.environ)"')
        assert not allowed

    def test_blocks_python_c_single_quote(self):
        allowed, reason = BashCommandFilter.check("python3 -c 'import os; print(os.environ)'")
        assert not allowed

    def test_blocks_python_e_flag(self):
        """python -e should be blocked."""
        allowed, reason = BashCommandFilter.check('python -e "print(1)"')
        assert not allowed

    def test_blocks_node_e(self):
        """node -e with inline code should be blocked."""
        allowed, reason = BashCommandFilter.check('node -e "console.log(process.env)"')
        assert not allowed

    def test_blocks_perl_e(self):
        """perl -e should be blocked."""
        allowed, reason = BashCommandFilter.check('perl -e "print %ENV"')
        assert not allowed

    def test_blocks_ruby_e(self):
        """ruby -e should be blocked."""
        allowed, reason = BashCommandFilter.check('ruby -e "puts ENV"')
        assert not allowed

    def test_blocks_python_dash_dash_e_c(self):
        """python3 --foo -c should be blocked."""
        allowed, reason = BashCommandFilter.check('python3 --warn -c "import os"')
        assert not allowed

    def test_blocks_python_dash_c_space(self):
        """python3 -c with space after flag should be blocked."""
        allowed, reason = BashCommandFilter.check('python3 -c "import os"')
        assert not allowed

    def test_blocks_os_environ_content(self):
        """Commands containing os.environ should be blocked."""
        allowed, reason = BashCommandFilter.check("python3 script.py")
        # script.py alone is allowed, but os.environ in the command should not appear
        assert allowed

    def test_blocks_shell_var_expansion(self):
        """$ANTHROPIC and similar should be blocked."""
        allowed, reason = BashCommandFilter.check("echo $ANTHROPIC_AUTH_TOKEN")
        assert not allowed

    def test_blocks_shell_var_brace_expansion(self):
        """${ANTHROPIC_BASE_URL} should be blocked."""
        allowed, reason = BashCommandFilter.check("echo ${ANTHROPIC_BASE_URL}")
        assert not allowed

    def test_blocks_shell_var_secret(self):
        """$SECRET, $TOKEN, $PASSWORD should be blocked."""
        allowed, reason = BashCommandFilter.check("echo $SECRET_KEY")
        assert not allowed

    def test_blocks_process_env(self):
        """process.env access should be blocked."""
        allowed, reason = BashCommandFilter.check('node -e "console.log(process.env.API_KEY)"')
        assert not allowed

    def test_allows_python_script_file(self):
        """Running a .py file without -c should be allowed."""
        allowed, reason = BashCommandFilter.check("python3 src/main.py --port 8000")
        assert allowed

    def test_allows_node_script_file(self):
        """Running a .js file without -e should be allowed."""
        allowed, reason = BashCommandFilter.check("node src/server.js")
        assert allowed

    def test_blocks_env_in_pipeline(self):
        """env in a pipeline should be blocked."""
        allowed, reason = BashCommandFilter.check("cat config.txt | env | grep API")
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
