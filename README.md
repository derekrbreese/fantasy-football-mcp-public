# Fantasy Football MCP Server

A Model Context Protocol (MCP) server for Yahoo Fantasy Football. It exposes league, roster, matchup, waiver-wire, draft, and lineup-analysis data to AI clients while keeping the underlying fantasy data source separate from the model.

## Current status

The project is usable today as a local or single-user MCP server. Yahoo Fantasy Sports API access now requires manual approval from Yahoo, and Yahoo currently provides read access only. Write actions such as adding/dropping players or changing lineups are therefore not part of the supported public tool surface.

The repository also contains the first stage of multi-user hardening: Yahoo credentials can be bound to an individual async request instead of relying exclusively on process-wide environment variables, and Yahoo response-cache keys are isolated by user in request-scoped mode.

A production public ChatGPT app still needs an application-level identity layer and encrypted per-user Yahoo token storage before it should be exposed to multiple users.

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
- `ff_get_waiver_wire`
- `ff_get_draft_rankings`
- `ff_get_draft_recommendation`
- `ff_analyze_draft_state`
- `ff_analyze_reddit_sentiment`

The server also contains maintenance tools used for local operation and troubleshooting. A public consumer deployment should expose only the user-facing read/analysis tools it actually needs.

## Installation

```bash
git clone https://github.com/derekrbreese/fantasy-football-mcp-public.git
cd fantasy-football-mcp-public
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and provide your Yahoo developer credentials. Do not commit `.env`, Yahoo token JSON files, OAuth state, refresh tokens, or other authentication artifacts.

## Yahoo API access

Creating a Yahoo developer application is no longer sufficient by itself to use the Fantasy Sports API. Apply for Fantasy API access through Yahoo's developer access process and associate the approval with your existing client ID.

Yahoo's current access model is read-only. This project therefore treats league-management recommendations separately from transaction execution.

## Authentication modes

### Local / legacy single-user mode

For local development, the server can continue to read these environment variables:

```env
YAHOO_CLIENT_ID=...
YAHOO_CLIENT_SECRET=...
YAHOO_ACCESS_TOKEN=...
YAHOO_REFRESH_TOKEN=...
YAHOO_GUID=...
```

### Request-scoped / multi-user foundation

Production code can bind one Yahoo credential set to the current async request:

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

Token refreshes in request-scoped mode remain inside that request context rather than mutating process-wide environment variables. Cached Yahoo responses are also namespaced per user.

For a public deployment, the values passed into `YahooCredentials` should come from an authenticated application session and an encrypted server-side credential store. Do not accept raw Yahoo tokens directly from model tool arguments.

## Running the MCP server

FastMCP HTTP server:

```bash
python fastmcp_server.py
```

By default the server listens on port 8000 locally. Cloud platforms can set `PORT`.

Traditional stdio MCP entry point:

```bash
python fantasy_football_multi_league.py
```

Docker:

```bash
docker build -t fantasy-football-mcp .
docker run --env-file .env -p 8080:8080 fantasy-football-mcp
```

Authentication files and token JSON files are explicitly excluded from the Docker build context.

## Testing

```bash
pytest
```

Credential-isolation tests cover request-scoped token handling and user-namespaced cache keys.

## Security notes

- Never commit Yahoo access or refresh tokens.
- Never bake user tokens into a container image.
- Do not use one process-wide Yahoo token for a multi-user deployment.
- Namespace caches by authenticated user.
- Store production refresh tokens encrypted at rest.
- Keep Yahoo client secrets server-side.
- Rotate any credential that has ever been committed to a public Git history.

If a secret was previously committed, deleting the current file is not sufficient by itself: revoke/rotate the credential and, when appropriate, rewrite the repository history.

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

## Public-app path

The core fantasy logic and MCP transport already exist. The remaining production work is primarily application infrastructure: authenticate the ChatGPT/app user, complete Yahoo OAuth for that user, store Yahoo refresh tokens securely per user, bind the resulting credential record to each MCP request, and expose a smaller consumer-facing tool set for review and launch.

## License

See `LICENSE`.
