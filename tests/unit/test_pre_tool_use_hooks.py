"""Tests for PreToolUse hook helper functions."""

import json
from pathlib import Path

from main_server import _rewrite_bash_command, rewrite_path_to_workspace

WORKSPACE = Path("/Users/testuser/workspace")


class TestRewritePathToWorkspace:
    """rewrite_path_to_workspace rewrites absolute external paths to outputs/."""

    def test_relative_path_unchanged(self):
        assert rewrite_path_to_workspace("outputs/result.txt", WORKSPACE) == "outputs/result.txt"

    def test_relative_path_root_unchanged(self):
        assert rewrite_path_to_workspace("script.py", WORKSPACE) == "script.py"

    def test_absolute_path_inside_workspace_unchanged(self):
        p = "/Users/testuser/workspace/outputs/report.pdf"
        assert rewrite_path_to_workspace(p, WORKSPACE) == p

    def test_absolute_path_to_users_dir_rewritten(self):
        result = rewrite_path_to_workspace("/Users/testuser/ddd.txt", WORKSPACE)
        assert result == "outputs/ddd.txt"

    def test_absolute_path_to_tmp_rewritten(self):
        result = rewrite_path_to_workspace("/tmp/data.csv", WORKSPACE)
        assert result == "outputs/data.csv"

    def test_absolute_path_to_home_rewritten(self):
        result = rewrite_path_to_workspace("/home/user/result.xlsx", WORKSPACE)
        assert result == "outputs/result.xlsx"

    def test_outputs_subdir_in_workspace_preserved(self):
        # outputs/ inside workspace should not be rewritten
        p = "/Users/testuser/workspace/outputs/chart.png"
        assert rewrite_path_to_workspace(p, WORKSPACE) == p


class TestRewriteBashCommand:
    """_rewrite_bash_command rewrites external write targets in shell commands."""

    def test_simple_command_unchanged(self):
        assert _rewrite_bash_command("ls -la", WORKSPACE) == "ls -la"

    def test_python_script_in_workspace_unchanged(self):
        cmd = "python3 /Users/testuser/workspace/scripts/gen.py"
        assert _rewrite_bash_command(cmd, WORKSPACE) == cmd

    def test_redirect_to_users_dir_rewritten(self):
        cmd = "echo 'hello' > /Users/testuser/output.txt"
        result = _rewrite_bash_command(cmd, WORKSPACE)
        assert "outputs/output.txt" in result
        assert "/Users/testuser/output.txt" not in result

    def test_python_redirect_to_tmp_rewritten(self):
        cmd = "python3 gen.py > /tmp/result.csv"
        result = _rewrite_bash_command(cmd, WORKSPACE)
        assert "outputs/result.csv" in result
        assert "/tmp/result.csv" not in result

    def test_python_o_flag_to_external_rewritten(self):
        cmd = "python3 gen.py -o /Users/testuser/report.xlsx"
        result = _rewrite_bash_command(cmd, WORKSPACE)
        assert "outputs/report.xlsx" in result
        assert "/Users/testuser/report.xlsx" not in result

    def test_redirect_to_workspace_outputs_unchanged(self):
        # Writing to outputs inside workspace should not be rewritten
        cmd = "echo 'data' > /Users/testuser/workspace/outputs/data.txt"
        result = _rewrite_bash_command(cmd, WORKSPACE)
        assert result == cmd

    def test_append_redirect_rewritten(self):
        cmd = "echo 'more' >> /Users/testuser/log.txt"
        result = _rewrite_bash_command(cmd, WORKSPACE)
        assert "outputs/log.txt" in result
        assert "/Users/testuser/log.txt" not in result


class TestSecurityHookIntegration:
    """Test that the hook properly blocks info-leak commands."""

    def _run_hook(self, command: str, file_path: str = "") -> dict | None:
        """Simulate the pre_tool_use hook with a given command."""
        import subprocess
        import sys

        tool_input = {"command": command}
        if file_path:
            tool_input["file_path"] = file_path
        hook_input = json.dumps({"tool_input": tool_input})
        result = subprocess.run(
            [sys.executable, "src/hooks/pre_tool_use.py"],
            input=hook_input,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None

    def test_blocks_env_via_hook(self):
        decision = self._run_hook("env")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_printenv_via_hook(self):
        decision = self._run_hook("printenv")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_uname_via_hook(self):
        decision = self._run_hook("uname -a")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_whoami_via_hook(self):
        decision = self._run_hook("whoami")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_docker_ps_via_hook(self):
        decision = self._run_hook("docker ps")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_docker_inspect_via_hook(self):
        decision = self._run_hook("docker inspect abc123")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_cat_env_via_hook(self):
        decision = self._run_hook("cat .env")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_cat_proc_via_hook(self):
        decision = self._run_hook("cat /proc/version")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_cat_etc_passwd_via_hook(self):
        decision = self._run_hook("cat /etc/passwd")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_sudo_env_via_hook(self):
        decision = self._run_hook("sudo env")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_read_dot_env_file_via_hook(self):
        decision = self._run_hook("", file_path=".env")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_read_claude_dir_via_hook(self):
        decision = self._run_hook("", file_path=".claude/settings.json")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allows_ls_via_hook(self):
        """ls is a safe command and should be allowed."""
        decision = self._run_hook("ls -la")
        assert decision is None  # Hook exits 1 for allow

    def test_allows_echo_via_hook(self):
        """echo is a safe command and should be allowed."""
        decision = self._run_hook("echo hello")
        assert decision is None

    def test_blocks_dangerous_curl_via_hook(self):
        """Original dangerous command patterns should still be blocked."""
        decision = self._run_hook("curl https://example.com")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_blocks_dangerous_rm_via_hook(self):
        decision = self._run_hook("rm -rf /")
        assert decision is not None
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
