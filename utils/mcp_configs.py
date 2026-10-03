"""Keep MCP client configs in step with freshly issued Yahoo tokens.

Shared by setup_yahoo_auth.py, refresh_yahoo_token.py and reauth_yahoo.py.

The server loads .env without overriding variables that are already set, so a
token stored in a client's MCP config wins over the fresh one in .env. Clients
that keep tokens in their config (Claude Desktop, Cursor, Antigravity) get them
rewritten here. Claude Code is only inspected, never written: ~/.claude.json is
Claude Code's live state file and it rewrites the whole file while running, so
an edit could be silently reverted. Claude Code doesn't need tokens in its
config anyway; the server reads them from .env.
"""

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SERVER_NAMES = ("fantasy-football", "yahoo-fantasy-football")
SERVER_SCRIPTS = ("fantasy_football_multi_league.py", "fastmcp_server.py")
TOKEN_KEYS = ("YAHOO_ACCESS_TOKEN", "YAHOO_REFRESH_TOKEN")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STDIO_SERVER = PROJECT_ROOT / "fantasy_football_multi_league.py"


def _is_this_server(name, entry):
    """Match by the usual names, or by a command that runs this repo's server."""
    if name in SERVER_NAMES:
        return True
    if not isinstance(entry, dict):
        return False
    parts = [str(entry.get("command", ""))] + [str(a) for a in entry.get("args") or []]
    return any(script in part for part in parts for script in SERVER_SCRIPTS)


def _desktop_config_path():
    system = platform.system()
    if system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if system == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _update_token_config(path, access_token, refresh_token, guid):
    """Write tokens into the first matching server entry. Returns True if one was updated."""
    with open(path) as f:
        config = json.load(f)
    servers = config.get("mcpServers") or {}
    for name, entry in servers.items():
        if name not in SERVER_NAMES:
            continue
        env = entry.setdefault("env", {})
        env["YAHOO_ACCESS_TOKEN"] = access_token
        env["YAHOO_REFRESH_TOKEN"] = refresh_token
        if guid:
            env["YAHOO_GUID"] = guid
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
        return True
    return False


def _quote(path):
    text = str(path)
    if platform.system() == "Windows":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def _scope_command(command, scope):
    """Anchor directory-scoped CLI operations to the checkout being inspected."""
    if scope in ("local", "project"):
        cd = "cd /d" if platform.system() == "Windows" else "cd"
        return f"{cd} {_quote(PROJECT_ROOT)} && {command}"
    return command


def _add_command(name="yahoo-fantasy-football", scope="user"):
    command = f"claude mcp add {_quote(name)} --scope {scope} -- {_quote(sys.executable)} {_quote(STDIO_SERVER)}"
    return _scope_command(command, scope)


def _claude_code_entries():
    """(scope, name, entry) in user scope and this checkout's local/project scopes."""
    found = []
    state_file = Path.home() / ".claude.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        for name, entry in (state.get("mcpServers") or {}).items():
            if _is_this_server(name, entry):
                found.append(("user", name, entry))
        for project, settings in (state.get("projects") or {}).items():
            # Local scope is keyed by the CLI's cwd string, not filesystem identity.
            # Aliases must not count as the key targeted by our repair commands.
            if project != str(PROJECT_ROOT):
                continue
            for name, entry in ((settings or {}).get("mcpServers") or {}).items():
                if _is_this_server(name, entry):
                    found.append(("local", name, entry))
    project_file = PROJECT_ROOT / ".mcp.json"
    if project_file.exists():
        try:
            with open(project_file) as f:
                servers = json.load(f).get("mcpServers") or {}
        except (OSError, ValueError):
            servers = {}
        for name, entry in servers.items():
            if _is_this_server(name, entry):
                found.append(("project", name, entry))
    return found


def _report_claude_code():
    """Print Claude Code status. Returns True if Claude Code is installed or configured."""
    installed = shutil.which("claude") is not None or (Path.home() / ".claude.json").exists()
    entries = _claude_code_entries()
    for scope, name, entry in entries:
        env = entry.get("env") or {}
        if any(key in env for key in TOKEN_KEYS):
            print(
                f"⚠️  Claude Code server '{name}' ({scope} scope) stores Yahoo tokens in its config.\n"
                "   Those override the fresh tokens in .env and expire within an hour.\n"
                "   Re-register it without them (the server reads .env on its own):\n"
                f"     {_scope_command(f'claude mcp remove {_quote(name)} --scope {scope}', scope)}\n"
                f"     {_add_command(name, scope)}"
            )
        else:
            print(
                f"✅ Claude Code server '{name}' ({scope} scope) reads tokens from .env, nothing to update"
            )

    if installed and not entries:
        print(
            "ℹ️  Claude Code is installed but this server isn't registered with it. To add it:\n"
            f"     {_add_command()}"
        )
    return installed or bool(entries)


def update_mcp_configs(access_token, refresh_token, guid=None):
    """Update MCP client configs with new tokens and report on Claude Code."""
    updated = []
    clients = [
        ("Claude Desktop config", _desktop_config_path()),
        ("Cursor MCP config", Path.home() / ".cursor" / "mcp.json"),
        ("Antigravity MCP config", Path.home() / ".gemini" / "antigravity" / "mcp_config.json"),
    ]
    for label, path in clients:
        if not path.exists():
            continue
        try:
            if _update_token_config(path, access_token, refresh_token, guid):
                updated.append(label)
        except Exception as e:
            print(f"⚠️  Could not update {label}: {e}")

    if updated:
        print(f"✅ Updated tokens in: {', '.join(updated)}")

    has_claude_code = _report_claude_code()

    if not updated and not has_claude_code:
        print("⚠️  No MCP client configs found to update (tokens are saved in .env)")
