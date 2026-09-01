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

Live-draft recommendations can run without Yahoo Fantasy API approval after a local
DraftSheets/CSV/JSON profile is imported from the private dashboard. For timestamped
injury and recent-news evidence, set the optional FantasyPros public API key in the
same ignored `.env` file:

```env
FANTASY_PROS_API=...
```

The key is sent only in the `x-api-key` header to FantasyPros. It is never returned,
logged, placed in browser storage, or written to either local draft-state file.
The provider serializes requests at just over one per second, caches successful
snapshots, limits cold targeted identity lookups, and backs off all provider calls for
fifteen minutes after a rate-limit response. Before every outbound call it atomically
reserves one of 95 requests in a persistent UTC-day budget, leaving a safety margin
below the public API's 100-request daily ceiling across local server restarts. Other
applications using the same API key still consume the provider's account-wide quota,
so the local counter cannot guarantee remaining provider capacity. The private
`~/.fantasy-football-mcp/fantasypros-request-budget.json` file contains only a schema
version, UTC date, and count; its directory and file use `0700` and `0600` permissions.
If that counter is exhausted or cannot be updated safely, no request is made and the
unavailable injury/news evidence remains explicitly unknown.

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

The optional [Yahoo Fantasy Draft Recorder](chrome-extension/README.md) works in Firefox and Chrome. It watches a logged-in Yahoo live draft, captures the full **Results → Round by Round** ledger, saves picks locally in the browser, and exports CSV or agent-ready JSON. A coherent authoritative table replaces the numbered stored/server-bound ledger exactly, preserving missing and duplicate numbers so recommendations remain blocked; conservatively unmatched unnumbered observations are retained. The popup surfaces those exact defects, while conflicting tables produce a recovery error. Automatic scans never lower a saved authoritative maximum from a shorter visible prefix. Its explicit **Full rescan & repair** action is the only downward path and stages a replacement only after responsive Yahoo tables agree, every nonempty row has the expected shape and parses, and the complete current authoritative ledger is contiguous and current; lowering the saved maximum also requires Yahoo's live current-pick evidence. The local server must accept the marked repair before a league-scoped browser write. Per-league storage, an extension-background lock broker for same-league scan/repair serialization, server-timestamped reset tombstones, and durable repair/reset journals prevent stale resurrection and cross-league clobber while ensuring interrupted operations are reconciled before stale scanning or sync resumes. Bounded broker heartbeats and an expiring `storage.session` fence preserve ordering across Firefox event-page or Chrome service-worker restarts; a lost lease cooperatively aborts protected network/storage work and leaves durable repair/reset state for reconciliation. The broker exchanges only an allowlisted session key and never exposes Yahoo page data to the background process. The recorder never stores Yahoo authentication data.

When this FastMCP server is running locally on port 8765 (`HOST=127.0.0.1 PORT=8765 python fastmcp_server.py`), the extension also syncs sanitized draft context to a loopback-only endpoint. Agents can call `ff_get_live_draft_state` to retrieve every pick, your roster, and rosters grouped by fantasy team before advising on the next selection. Local server state is written to `~/.fantasy-football-mcp/live-drafts.json` with user-only permissions.

Firefox also provides a persistent **Draft Assistant** sidebar for top-five recommendations alongside Yahoo, while the recorder popup keeps scan, repair, export, and mock-reset controls. The sidebar uses only the active Yahoo draft league or an explicit saved-league choice; it never silently selects the newest session. After its first request, exact-league pick updates cancel stale work, debounce duplicate storage events for 350 ms, and automatically refresh from the newer saved snapshot. A wider loopback-only dashboard at `http://127.0.0.1:8765/draft-dashboard` provides a configurable board of up to twenty candidates, roster construction, recent draft history, specialist comparisons, critic checks, and data-source diagnostics. Both surfaces share the same safe text-only renderer, show exact ledger blockers and uncertainty/degradation labels, and are recommendation-only: they never inject controls into Yahoo or draft a player.

For repeated Yahoo mock drafts, open the exact active draft tab and select **Reset mock draft** in the popup. After explicit confirmation, the extension resets only that session in browser and loopback-server state, preserves its separately imported DraftSheets profile, and rescans only after the server reset timestamp. Close older tabs for that same mock/session before continuing. If the server reports that the draft changed, rescan and confirm Reset again. **Full rescan & repair** is different: use it when the current draft should be kept but its saved ledger has gaps, duplicates, or a phantom pick.

Starting a separate Yahoo mock creates a new draft identity, so its prior mock's exact
profile is not reused silently. Open **Full dashboard**, choose the prior entry under
**Reuse a saved profile**, and select **Use for this mock & refresh**. The dashboard
lists only safe source metadata, labels each choice with its sport and source date (or
import date), and sends only the explicitly chosen source and current target league IDs.
The server resolves the target sport and rejects a mismatched source. It copies sanitized
rankings, roster settings, and provenance onto the new exact identity, never copies
either draft's picks, and immediately retries the recommendation. Saved-profile list
and bind requests have bounded local timeouts. Importing the workbook again remains
available when its settings should change.

The dashboard can also import a league-bound local draft profile. The supported
DraftSheets 2026 workbook is parsed in memory, reduced to the top 500 allowlisted ECR
rows and roster settings, and then discarded; CSV/JSON inputs are allowlisted in the
browser before posting. Raw workbooks are limited to 2 MB and never persist, and
filenames, URLs, formulas, notes, and arbitrary cells are excluded. Sanitized profiles
are isolated by the recorder's exact sport/league/team/session identity and stored at
`~/.fantasy-football-mcp/draft-profiles.json` with user-only permissions. When an exact
profile exists, recommendations use its rankings and league settings and make zero
Yahoo API calls. Yahoo remains a fallback only when no matching profile exists.

The UI sends only an allowlisted league ID and bounded strategy/count/ranking/simulation settings to `POST /draft-recommendation`; it never resends the ledger, team ID, session key, credentials, cookies, page URLs, query parameters, or arbitrary browser fields. The server independently resolves exactly one saved session. It either loads an exact-identity local profile, or resolves the Yahoo league key, verifies the authenticated Yahoo team, and serializes Yahoo calls before bounded deterministic scoring. It rechecks both the draft snapshot and selected profile after scoring and suppresses every candidate if either changed. The route accepts only loopback clients with an exact local-dashboard or extension origin and returns no-store responses.

Call `ff_get_live_draft_recommendation` with `league_id` during an API-free draft, or with a Yahoo `league_key` when using the Yahoo fallback. One in-process orchestrator filters drafted players and combines focused value, roster-construction, positional-run, opponent-survival, risk/news, deterministic simulation, and critic components. It returns a primary pick, alternatives, confidence, estimated return probability, roster impact, risks, recent allowlisted headlines, and a contingency. FantasyPros calls happen only in the service layer and are one-per-second paced, cached, failure-backed-off, bounded, and reserved against this app's persistent 95-call UTC-day budget under the [public API limits](https://api.fantasypros.com/public/v2/terms-of-use); the scorer accepts only attributed fresh status and structured news categories and never interprets arbitrary article text. Current injury status uses a recent provider snapshot, recent news expires by publication time, limited, rate-limited, budget-exhausted, or budget-unavailable provider coverage is explicit, and every missing/unresolved/stale record remains unknown. Unknown per-player risk is removed from that candidate's weights rather than treated as healthy or neutral. The opponent and simulation probabilities are explicitly labeled as uncalibrated heuristics. Recommendations are blocked when the pick ledger is incomplete, duplicated, or unnumbered.

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
