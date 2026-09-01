# Fantasy Football MCP Server

A personal, single-user Model Context Protocol (MCP) server for Yahoo Fantasy Football. It exposes league, roster, matchup, waiver-wire, draft, and lineup-analysis data to AI clients while keeping the underlying fantasy data source separate from the model.

## Current status

This project is built and run as a **single-user app**: you supply your own Yahoo developer credentials and tokens, and the server serves your leagues to your MCP client. It is not intended to be deployed by someone else as a shared, multi-user service.

That said, the codebase deliberately includes groundwork for a future multi-user version — request-scoped Yahoo credentials and per-user cache isolation — so that when the app is submitted to the ChatGPT app store, the core plumbing is already in place. See [ChatGPT app store readiness](#chatgpt-app-store-readiness) below. You do not need any of that to run the app yourself.

Note that Yahoo Fantasy Sports API access now requires manual approval from Yahoo, and Yahoo currently provides read access only. Write actions such as adding/dropping players or changing lineups are therefore not part of the tool surface.

Contributors and coding agents should read [AGENTS.md](AGENTS.md) for architecture, safety invariants, and validation commands. Prioritized follow-up work is tracked in [IMPROVEMENTS.md](IMPROVEMENTS.md).

## Core capabilities

- Multi-league Yahoo fantasy football discovery
- League settings and standings
- Team rosters and weekly matchups
- Free-agent and waiver-wire research
- Team comparisons
- Draft rankings, recommendations, and draft-state analysis
- Lineup optimization
- Optional external player-context/enrichment integrations

## MCP tools

The main FastMCP server currently exposes:

- `ff_get_leagues`
- `ff_get_league_info`
- `ff_get_standings`
- `ff_get_roster`
- `ff_get_matchup`
- `ff_get_players`
- `ff_compare_teams`
- `ff_build_lineup`
- `ff_get_draft_results`
- `ff_get_live_draft_state`
- `ff_get_live_draft_recommendation`
- `ff_get_waiver_wire`
- `ff_get_draft_rankings`
- `ff_get_draft_recommendation`
- `ff_analyze_draft_state`
- `ff_analyze_reddit_sentiment`

The server also contains maintenance tools used for local operation and troubleshooting.

## Installation

```bash
git clone https://github.com/evanchen7/fantasy-football-mcp-public.git
cd fantasy-football-mcp-public
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and provide your Yahoo developer credentials. Do not commit `.env`, Yahoo token JSON files, OAuth state, refresh tokens, or other authentication artifacts.

## Yahoo API access

Creating a Yahoo developer application is no longer sufficient by itself to use the Fantasy Sports API. Apply for Fantasy API access through Yahoo's developer access process and associate the approval with your existing client ID.

Yahoo's current access model is read-only. This project therefore treats league-management recommendations separately from transaction execution.

## Authentication

The server reads your Yahoo credentials from environment variables:

```env
YAHOO_CLIENT_ID=...
YAHOO_CLIENT_SECRET=...
YAHOO_ACCESS_TOKEN=...
YAHOO_REFRESH_TOKEN=...
YAHOO_GUID=...
```

This single-user mode is the supported way to run the app.

## Browser extension: live draft recording

The optional [Yahoo Fantasy Draft Recorder](chrome-extension/README.md) works in Firefox and Chrome. It watches a logged-in Yahoo live draft, captures the full **Results → Round by Round** ledger, saves picks locally in the browser, and exports CSV or agent-ready JSON. A coherent authoritative table replaces the numbered stored/server-bound ledger exactly, preserving missing and duplicate numbers so recommendations remain blocked; conservatively unmatched unnumbered observations are retained. The popup surfaces those exact defects, while conflicting tables produce a recovery error. Automatic scans never lower a saved authoritative maximum from a shorter visible prefix. Its explicit **Full rescan & repair** action is the only downward path and stages a replacement only after responsive Yahoo tables agree, every nonempty row has the expected shape and parses, and the complete current authoritative ledger is contiguous and current; lowering the saved maximum also requires Yahoo's live current-pick evidence. The local server must accept the marked repair before a league-scoped browser write. Per-league storage, same-league scan/repair serialization, timestamped Clear tombstones, and a durable repair journal prevent stale resurrection and cross-league clobber while ensuring interrupted repair persistence is reconciled before stale scanning or sync resumes. It never stores Yahoo authentication data.

When this FastMCP server is running locally on port 8765 (`HOST=127.0.0.1 PORT=8765 python fastmcp_server.py`), the extension also syncs sanitized draft context to a loopback-only endpoint. Agents can call `ff_get_live_draft_state` to retrieve every pick, your roster, and rosters grouped by fantasy team before advising on the next selection. Local server state is written to `~/.fantasy-football-mcp/live-drafts.json` with user-only permissions.

Call `ff_get_live_draft_recommendation` with a Yahoo `league_key` during the draft. One in-process orchestrator filters drafted players and combines focused value, roster-construction, positional-run, opponent-survival, risk/news, deterministic simulation, and critic components. It returns a primary pick, alternatives, confidence, estimated return probability, roster impact, risks, and a contingency. The opponent and simulation probabilities are explicitly labeled as uncalibrated heuristics; optional news is used only when a ranking source includes timestamped attribution, and missing news is reported as unknown rather than healthy. Recommendations are blocked when the pick ledger is incomplete, duplicated, or unnumbered.

For Firefox, load `chrome-extension/manifest.json` from **This Firefox** in `about:debugging`. See the extension README for full setup, privacy, persistence, and testing instructions.

## Running the MCP server

FastMCP HTTP server:

```bash
HOST=127.0.0.1 python fastmcp_server.py
```

By default the server listens on port 8000. Always bind local sessions to `127.0.0.1`; the MCP HTTP transport is not authenticated. Container and cloud platforms that provide their own access boundary can explicitly set `HOST=0.0.0.0` and `PORT`.

Traditional stdio MCP entry point:

```bash
python fantasy_football_multi_league.py
```

Docker:

```bash
docker build -t fantasy-football-mcp .
docker run --env-file .env -e HOST=0.0.0.0 -e PORT=8080 -p 8080:8080 fantasy-football-mcp
```

Authentication files and token JSON files are explicitly excluded from the Docker build context.

## Testing

```bash
pytest
```

Credential-isolation tests cover request-scoped token handling and user-namespaced cache keys.

## Security notes

Even as a single-user app, keep credentials out of the repository:

- Never commit Yahoo access or refresh tokens.
- Never bake your tokens into a container image.
- Keep your Yahoo client secret server-side.
- Rotate any credential that has ever been committed to a public Git history.

If a secret was previously committed, deleting the current file is not sufficient by itself: revoke/rotate the credential and, when appropriate, rewrite the repository history.

## ChatGPT app store readiness

This app runs single-user today, but the code is structured so it can become a public ChatGPT app later without a rewrite. The multi-user groundwork already in the codebase includes:

- **Request-scoped credentials** — `src/api/yahoo_credentials.py` can bind one Yahoo credential set to the current async request instead of relying on process-wide environment variables:

```python
from src.api.yahoo_credentials import YahooCredentials, use_yahoo_credentials

credentials = YahooCredentials(
    access_token=user_access_token,
    refresh_token=user_refresh_token,
    client_id=app_client_id,
    client_secret=app_client_secret,
    user_id=user_id,
)

with use_yahoo_credentials(credentials):
    # Yahoo calls made in this context use only this user's credentials.
    ...
```

- **Isolated token refresh** — token refreshes in request-scoped mode stay inside that request context rather than mutating process-wide environment variables.
- **Per-user cache namespacing** — cached Yahoo responses are keyed by user in request-scoped mode.

What remains before an app store submission is application infrastructure rather than fantasy logic: authenticate the ChatGPT user, complete Yahoo OAuth for that user, store Yahoo refresh tokens encrypted per user, bind the resulting credential record to each MCP request, and trim the exposed tool set to the consumer-facing read/analysis tools needed for review and launch.

## Project structure

```text
fantasy-football-mcp-public/
├── fastmcp_server.py
├── fantasy_football_multi_league.py
├── lineup_optimizer.py
├── matchup_analyzer.py
├── position_normalizer.py
├── src/
│   ├── api/
│   │   ├── yahoo_client.py
│   │   └── yahoo_credentials.py
│   ├── agents/
│   ├── handlers/
│   ├── models/
│   ├── services/
│   └── strategies/
├── tests/
├── utils/
├── Dockerfile
└── requirements.txt
```

## License

See `LICENSE`.
