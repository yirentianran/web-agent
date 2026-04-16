"""Tests for PreToolUse hook helper functions."""

from pathlib import Path

from main_server import rewrite_path_to_workspace, _rewrite_bash_command

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
