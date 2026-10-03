"""MCP client config updates after Yahoo auth (issue #31: Claude Code wasn't detected)."""

import json

import pytest

from utils import mcp_configs


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated home directory with no MCP clients and no `claude` on PATH."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(mcp_configs.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(mcp_configs.shutil, "which", lambda name: None)
    monkeypatch.setattr(mcp_configs, "PROJECT_ROOT", tmp_path / "repo")
    return tmp_path


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def stdio_entry(**env):
    entry = {"command": "/venv/bin/python", "args": ["/repo/fantasy_football_multi_league.py"]}
    if env:
        entry["env"] = env
    return entry


def test_nothing_installed_says_tokens_are_in_env(home, capsys):
    mcp_configs.update_mcp_configs("acc", "ref")
    assert "No MCP client configs found to update (tokens are saved in .env)" in capsys.readouterr().out


def test_claude_code_installed_without_server_prints_add_command(home, capsys, monkeypatch):
    monkeypatch.setattr(mcp_configs.shutil, "which", lambda name: "/usr/local/bin/claude")
    mcp_configs.update_mcp_configs("acc", "ref")
    out = capsys.readouterr().out
    assert "Claude Code is installed but this server isn't registered" in out
    assert "claude mcp add yahoo-fantasy-football --scope user --" in out
    assert "fantasy_football_multi_league.py" in out
    assert "No MCP client configs found" not in out


def test_claude_code_user_scope_server_reads_env(home, capsys):
    write(home / ".claude.json", {"mcpServers": {"yahoo-fantasy-football": stdio_entry()}})
    mcp_configs.update_mcp_configs("acc", "ref")
    out = capsys.readouterr().out
    assert "'yahoo-fantasy-football' (user scope) reads tokens from .env" in out
    assert "No MCP client configs found" not in out


def test_claude_code_server_found_by_command_under_any_name(home, capsys):
    write(home / ".claude.json", {"projects": {"/work": {"mcpServers": {"ffb": stdio_entry()}}}})
    mcp_configs.update_mcp_configs("acc", "ref")
    assert "'ffb' (local scope) reads tokens from .env" in capsys.readouterr().out


def test_claude_code_tokens_in_config_are_flagged_not_rewritten(home, capsys):
    state = {"mcpServers": {"fantasy-football": stdio_entry(YAHOO_ACCESS_TOKEN="stale")}}
    write(home / ".claude.json", state)
    mcp_configs.update_mcp_configs("acc", "ref")
    out = capsys.readouterr().out
    assert "stores Yahoo tokens in its config" in out
    assert "claude mcp remove fantasy-football --scope user" in out
    assert "claude mcp add fantasy-football --scope user --" in out
    # Claude Code's live state file is never written.
    assert json.loads((home / ".claude.json").read_text()) == state


def test_project_scope_mcp_json_is_detected(home, capsys):
    write(home / "repo" / ".mcp.json", {"mcpServers": {"fantasy-football": stdio_entry()}})
    mcp_configs.update_mcp_configs("acc", "ref")
    assert "'fantasy-football' (project scope) reads tokens from .env" in capsys.readouterr().out


def test_unrelated_claude_code_servers_are_ignored(home, capsys, monkeypatch):
    monkeypatch.setattr(mcp_configs.shutil, "which", lambda name: "/usr/local/bin/claude")
    write(home / ".claude.json", {"mcpServers": {"github": {"command": "gh-mcp"}}})
    mcp_configs.update_mcp_configs("acc", "ref")
    assert "isn't registered" in capsys.readouterr().out


def test_cursor_tokens_still_rewritten(home, capsys):
    path = home / ".cursor" / "mcp.json"
    write(path, {"mcpServers": {"yahoo-fantasy-football": stdio_entry(YAHOO_ACCESS_TOKEN="old")}})
    mcp_configs.update_mcp_configs("acc", "ref", guid="G1")
    env = json.loads(path.read_text())["mcpServers"]["yahoo-fantasy-football"]["env"]
    assert env == {"YAHOO_ACCESS_TOKEN": "acc", "YAHOO_REFRESH_TOKEN": "ref", "YAHOO_GUID": "G1"}
    assert "Updated tokens in: Cursor MCP config" in capsys.readouterr().out


def test_claude_desktop_tokens_still_rewritten(home, capsys):
    path = mcp_configs._desktop_config_path()
    write(path, {"mcpServers": {"fantasy-football": stdio_entry()}})
    mcp_configs.update_mcp_configs("acc", "ref")
    assert json.loads(path.read_text())["mcpServers"]["fantasy-football"]["env"]["YAHOO_ACCESS_TOKEN"] == "acc"
    assert "Claude Desktop config" in capsys.readouterr().out
