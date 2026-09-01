# Installation

The canonical installation and usage guide is [README.md](README.md). It covers:

- Python 3.10+ installation with `uv` or a virtual environment
- Private `.env` setup for Yahoo and optional FantasyPros access
- The loopback-only server on `127.0.0.1:8765`
- Firefox and Chrome extension loading
- Live and Instant Mock Draft workflows
- Local rankings-profile import, recommendations, reset, and repair
- Yahoo Fantasy API provisioning and troubleshooting

Quick start:

```bash
git clone https://github.com/evanchen7/fantasy-football-mcp-public.git
cd fantasy-football-mcp-public
uv sync
cp .env.example .env
chmod 600 .env
HOST=127.0.0.1 PORT=8765 uv run python fastmcp_server.py
```

Keep `.env`, Yahoo tokens, OAuth state, and MCP configurations containing credentials out of Git. The server must remain bound to `127.0.0.1` for ordinary desktop use because its MCP transport has no local authentication boundary.

Continue with [Quick start: live or mock draft](README.md#quick-start-live-or-mock-draft).
