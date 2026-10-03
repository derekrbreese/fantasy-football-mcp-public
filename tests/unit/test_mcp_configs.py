"""MCP client config updates after Yahoo auth (issue #31: Claude Code wasn't detected)."""

import json
import os
import subprocess

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
    monkeypatch.setattr(
        mcp_configs, "STDIO_SERVER", tmp_path / "repo" / "fantasy_football_multi_league.py"
    )
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
    assert (
        "No MCP client configs found to update (tokens are saved in .env)"
        in capsys.readouterr().out
    )


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
    write(
        home / ".claude.json",
        {"projects": {str(home / "repo"): {"mcpServers": {"ffb": stdio_entry()}}}},
    )
    mcp_configs.update_mcp_configs("acc", "ref")
    assert "'ffb' (local scope) reads tokens from .env" in capsys.readouterr().out


@pytest.mark.parametrize("env", [{}, {"YAHOO_ACCESS_TOKEN": "stale"}])
def test_other_project_does_not_suppress_registration(home, capsys, env):
    state = {
        "projects": {str(home / "other"): {"mcpServers": {"fantasy-football": stdio_entry(**env)}}}
    }
    write(home / ".claude.json", state)
    mcp_configs.update_mcp_configs("acc", "ref")
    out = capsys.readouterr().out
    assert "isn't registered" in out
    assert "claude mcp add yahoo-fantasy-football --scope user --" in out
    assert "local scope" not in out
    assert "claude mcp remove" not in out
    assert json.loads((home / ".claude.json").read_text()) == state


def test_only_this_project_is_reported_when_multiple_projects_exist(home, capsys, monkeypatch):
    other = home / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    write(
        home / ".claude.json",
        {
            "projects": {
                str(home / "other"): {
                    "mcpServers": {"fantasy-football": stdio_entry(YAHOO_ACCESS_TOKEN="stale")}
                },
                str(home / "repo"): {"mcpServers": {"ffb": stdio_entry()}},
            }
        },
    )
    mcp_configs.update_mcp_configs("acc", "ref")
    out = capsys.readouterr().out
    assert "'ffb' (local scope) reads tokens from .env" in out
    assert "stores Yahoo tokens" not in out
    assert "isn't registered" not in out


def test_local_scope_resolves_project_path_alias(home, capsys):
    repo = home / "repo"
    repo.mkdir()
    alias = home / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    write(home / ".claude.json", {"projects": {str(alias): {"mcpServers": {"ffb": stdio_entry()}}}})
    mcp_configs.update_mcp_configs("acc", "ref")
    assert "'ffb' (local scope) reads tokens from .env" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="Executes POSIX shell instructions")
@pytest.mark.parametrize("scope", ["local", "project"])
def test_repair_commands_target_checkout_from_another_directory(home, capsys, monkeypatch, scope):
    repo = home / "repo with spaces and 'quotes'"
    repo.mkdir()
    monkeypatch.setattr(mcp_configs, "PROJECT_ROOT", repo)
    monkeypatch.setattr(mcp_configs, "STDIO_SERVER", repo / "fantasy_football_multi_league.py")
    servers = {"fantasy-football": stdio_entry(YAHOO_REFRESH_TOKEN="stale")}
    if scope == "local":
        path = home / ".claude.json"
        state = {"projects": {str(repo): {"mcpServers": servers}}}
    else:
        path = repo / ".mcp.json"
        state = {"mcpServers": servers}
    write(path, state)
    other = home / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    mcp_configs.update_mcp_configs("acc", "ref")
    out = capsys.readouterr().out
    commands = [line.strip() for line in out.splitlines() if "claude mcp " in line]
    assert len(commands) == 2
    # Execute the printed instructions against a stub CLI, never the real client.
    bin_dir = home / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "claude"
    cli.write_text(
        '#!/bin/sh\npwd >> "$CLAUDE_CWD_LOG"\nprintf "%s\\n" "$*" >> "$CLAUDE_ARG_LOG"\n'
    )
    cli.chmod(0o755)
    cwd_log = home / "cwd.log"
    arg_log = home / "args.log"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "CLAUDE_CWD_LOG": str(cwd_log),
        "CLAUDE_ARG_LOG": str(arg_log),
    }
    for command in commands:
        subprocess.run(command, shell=True, cwd=other, env=env, check=True)
    assert cwd_log.read_text().splitlines() == [str(repo.resolve()), str(repo.resolve())]
    remove, add = arg_log.read_text().splitlines()
    assert remove == f"mcp remove fantasy-football --scope {scope}"
    assert add.startswith(f"mcp add fantasy-football --scope {scope} -- ")
    assert str(repo / "fantasy_football_multi_league.py") in add
    assert json.loads(path.read_text()) == state


def test_windows_directory_scoped_commands_include_drive_change(home, monkeypatch):
    monkeypatch.setattr(mcp_configs.platform, "system", lambda: "Windows")
    monkeypatch.setattr(mcp_configs, "PROJECT_ROOT", mcp_configs.Path(r"D:\Fantasy Football"))
    command = mcp_configs._add_command("fantasy-football", "local")
    assert command.startswith(
        'cd /d "D:\\Fantasy Football" && claude mcp add fantasy-football --scope local -- '
    )


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
    assert (
        json.loads(path.read_text())["mcpServers"]["fantasy-football"]["env"]["YAHOO_ACCESS_TOKEN"]
        == "acc"
    )
    assert "Claude Desktop config" in capsys.readouterr().out
