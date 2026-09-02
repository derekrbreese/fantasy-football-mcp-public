# Fantasy Football MCP Server

A private, single-user Yahoo Fantasy Football MCP server with a Firefox/Chrome draft recorder and local recommendation UI. Use it for ordinary league research through Yahoo, or run live draft recommendations from the browser recorder and a local rankings profile without Yahoo Fantasy API access.

The assistant is recommendation-only. It does not set a lineup, add or drop a player, or make a draft pick for you.

## Two ways to use it

- **Live draft assistant:** the extension records Yahoo's numbered draft ledger, the local server combines it with imported rankings and roster settings, and the sidebar/dashboard recommends the next pick. Yahoo API approval is not required when the current draft has a local profile.
- **Yahoo league tools:** an MCP client can inspect leagues, settings, standings, rosters, matchups, players, waivers, and completed drafts. These tools require a Yahoo developer app that Yahoo has approved for Fantasy Sports API access.

This project is intended to run for one user on their own computer. Keep the server bound to `127.0.0.1`; its MCP transport has no local authentication boundary.

## Quick start: live or mock draft

### 1. Install the project

Prerequisites:

- Python 3.10 or newer
- Firefox 142+ or Chrome 121+
- A Yahoo Fantasy account and an open live or mock draft
- A local `.xlsx`, `.csv`, or `.json` rankings file if Yahoo API access is unavailable

Clone the repository, then install its Python dependencies with `uv`:

```bash
git clone https://github.com/evanchen7/fantasy-football-mcp-public.git
cd fantasy-football-mcp-public
uv sync
```

The optional Databricks advisory critic uses public OSS SDK packages in a separate dependency extra. Install it only if you intend to opt in:

```bash
uv sync --extra databricks
```

Or use a virtual environment and `requirements.txt`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. Configure `.env`

Create a private local configuration file:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` locally. Do not paste real values into issues, chat, screenshots, browser storage, or MCP configuration checked into Git.

For API-free live draft recommendations, the Yahoo variables may remain unset. Optional FantasyPros injury/news enrichment needs only:

```env
FANTASY_PROS_API=your_key_here
```

Yahoo-backed league tools additionally need:

```env
YAHOO_CLIENT_ID=...
YAHOO_CLIENT_SECRET=...
YAHOO_ACCESS_TOKEN=...
YAHOO_REFRESH_TOKEN=...
YAHOO_GUID=...
```

The repository ignores `.env`, token JSON, OAuth state, and common MCP configuration files. Rotate any secret that has ever entered public Git history. Restart the server after changing an API key or any other `.env` value; the running process does not reload credentials automatically.

The optional Databricks advisory critic is disabled by default. Its setup and outbound-data boundary are documented in [Databricks advisory critic](#databricks-advisory-critic).

### 3. Start the private loopback server

Port 8000 is commonly occupied, so the extension and dashboard use 8765 in the local workflow:

```bash
HOST=127.0.0.1 PORT=8765 uv run python fastmcp_server.py
```

If you installed with `pip` in an activated environment, use `python fastmcp_server.py` instead. Leave this terminal running. The local dashboard is served at `http://127.0.0.1:8765/draft-dashboard`, and the HTTP MCP endpoint is `http://127.0.0.1:8765/mcp`.

Do not substitute `0.0.0.0` for ordinary desktop use. That is reserved for an intentionally secured container or cloud environment with its own access boundary.

### 4. Load the browser extension

Firefox:

1. Open `about:debugging`.
2. Select **This Firefox**.
3. Select **Load Temporary Add-on…**.
4. Choose `chrome-extension/manifest.json` from this checkout.

Chrome:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Choose the repository's `chrome-extension` directory.

Temporary/unpacked extensions must be reloaded after pulling extension changes. Firefox removes a temporary add-on when the browser restarts. See the [extension guide](chrome-extension/README.md) for browser details.

### 5. Record and sync the exact draft

Follow this order for each live draft or Yahoo mock:

1. Start the local server.
2. Open the exact active Yahoo draft tab, then reload that tab after loading or reloading the extension.
3. In Yahoo, open **Results → Round by Round**. This numbered ledger is the authoritative source.
4. Open the extension popup and select **Rescan page**.
5. Confirm the popup shows the expected league, pick count, a healthy contiguous ledger, and **agent sync connected**.
6. Select **Full dashboard**. Opening it from the popup carries the selected league ID in a browser fragment without putting that fragment in the initial dashboard GET.
7. Import or explicitly reuse a rankings profile for this exact draft if it does not already have one.
8. Open the Firefox **Draft Assistant** sidebar or use the full dashboard, then refresh recommendations.

The recorder continues watching new picks when other Yahoo draft panels are visible. In particular, the active Yahoo **Picks** tab is a strict secondary feed for its currently rendered cards, alongside the last-pick banner; **Queue** entries are never treated as drafted players. The recorder does not scroll that virtualized panel; it accumulates newly rendered cards through Yahoo DOM changes and manual rescans. Return to **Results → Round by Round** and rescan whenever the saved ledger is questionable because that complete numbered table remains the only authoritative source and the only repair input. Recommendations are deliberately blocked for missing, duplicate, or unnumbered picks, and whenever the recorder cannot verify authoritative ledger capture integrity.

### 6. Import rankings and league settings

The dashboard's **Local draft profile** section accepts:

- A supported DraftSheets 2026 `.xlsx` workbook, up to 2 MB
- An ECR `.csv` with required rank/ECR, player-name, and position columns; team, ADP, and bye are optional
- A strict `schemaVersion: 1` `.json` profile

Before importing, check the team count and roster slots shown in the form. The server keeps at most 500 sanitized ranking rows and binds them to the exact recorder identity. It does not store the raw workbook, filename, formulas, notes, URLs, or arbitrary cells.

Google Drive is optional storage only; this app does not read Drive or Google Sheets at runtime. Download the sheet to your computer as `.xlsx` or export its ECR tab as CSV, then choose that local file in the dashboard.

A matching local profile provides rankings and league settings with zero Yahoo API calls. Drafted players still come from the extension's live ledger. Yahoo's initialed player names are matched conservatively by name, position, and canonical NFL team; known equivalent provider codes such as Jacksonville's `JAX` and `JAC` are normalized before availability is decided.

### 7. Get recommendations

The easiest interfaces are:

- **Extension popup:** recorder status, ledger health, rescan/repair/reset controls, CSV export, and Agent JSON export. It does not show or make draft picks.
- **Firefox Draft Assistant:** a persistent top-five recommendation sidebar next to Yahoo. Chrome users should use the dashboard.
- **Full dashboard:** up to twenty candidates, roster construction, recent picks, specialist comparisons, critic checks, simulations, and data-source diagnostics.

An MCP client can use the same state explicitly. The safe order is:

1. Call `ff_get_live_draft_state` with the popup's `league_id` and inspect its ledger/identity warnings.
2. Only when that state is ready, call `ff_get_live_draft_recommendation` with the same `league_id`.

When using Yahoo fallback instead of a local profile, a validated Yahoo `league_key` may also be supplied. Confidence, opponent-return probability, and scenario probability are uncalibrated heuristics; injury/news evidence that is missing or stale remains **unknown**, never healthy.

## Instant Mock Drafts and repeated mocks

Yahoo Instant Mock Drafts use the same recorder flow. Scan the exact mock first, then import a local profile, explicitly bind a saved one, or configure a per-sport default before requesting advice. If an Instant Mock Draft identity is not discoverable through Yahoo's Fantasy API, this local-profile path is the expected recovery; refreshing an OAuth token does not make that mock a normal Yahoo league.

Each newly created Yahoo mock has a new identity. To reuse the same rankings without uploading them again:

1. Open and scan the new mock.
2. Open **Full dashboard** from its popup.
3. Under **Reuse a saved profile**, choose the prior source yourself.
4. Select **Use for this draft & refresh**.

Only sanitized rankings, roster settings, and provenance are copied. Picks are never copied between mocks.

To remove that manual step for later mocks, use **Default for future drafts** in the dashboard. Choose **Yahoo Football**, select the saved source, and select **Set sport default**. On the first recommendation for a newly synced draft with no exact profile, the server atomically binds that default to the new identity and continues without a Yahoo API call. An already bound or imported exact profile always wins, and changing or clearing the default does not alter prior drafts. Default selection and automatic binding require a source profile for the current UTC year, so import a new sheet and replace or clear the pointer after a season rollover.

Yahoo's draft-client URL does not reliably distinguish a mock from a real draft, and the recorder intentionally discards its query string. The default therefore applies to every future profileless recorder draft for that sport, including real drafts and mocks. The dashboard states this scope before you enable it; use manual binding instead if you draft in multiple leagues with different settings.

Use the two popup recovery controls for different jobs:

- **Reset mock draft:** start over within the same exact mock/session. Keep the server running, close older tabs for that same mock, open the active tab, rescan, and confirm Reset. The imported profile is preserved.
- **Full rescan & repair:** keep the current draft but replace a defective saved ledger. First show the complete current **Results → Round by Round** table. Repair is accepted only when the completed ledger is coherent, contiguous, unique, and current. Yahoo's pre-rendered unfilled future rows are excluded only when they have an unambiguous positive pick number at or beyond one stable, visible, non-conflicting live current-pick marker; malformed, earlier, or unverifiable rows still block repair and authoritative replacement.

An unsafe, ambiguous, or truncated authoritative scan preserves the prior numbered picks and syncs only a private boolean capture blocker for that exact league. The local recommender then stays blocked even if the older ledger looks contiguous. Numbered Picks-tab or banner observations captured before a verified Round-by-Round baseline are retained for recovery but also set this blocker. Only a later coherent Round-by-Round scan with a stable positive current-pick marker matching the ledger maximum, or a server-accepted repair, clears it. A coherent scan without current-pick evidence may update picks but preserves the prior capture decision; ordinary scans with no authoritative table cannot clear it. After a baseline exists, matching secondary observations deduplicate conservatively, while a different player reported for an already saved overall pick is retained as a duplicate. A secondary observation that later fills an already recorded gap is retained but also sets the capture blocker, so it cannot make the ledger recommendation-ready without Round-by-Round verification or repair.

If the server says the draft changed during reset, rescan and confirm Reset again. Neither operation can target a merely “latest” draft; the exact active identity is required.

## FantasyPros injury/news cache

Set `FANTASY_PROS_API` to enable attributed NFL injury status and recent-news evidence from the [FantasyPros public API](https://api.fantasypros.com/public/v2/docs#tag/News-and-Injuries). Restart the server after adding or replacing the key.

FantasyPros' [public API terms](https://api.fantasypros.com/public/v2/terms-of-use) instruct clients to cache data rather than poll unnecessarily and require FantasyPros attribution when publishing analysis based on it. This app labels the provider as FantasyPros and uses a private local cache to limit network traffic.

The integration is defensive by design:

- The key is sent only as FantasyPros' `x-api-key` header and is never returned to the browser, logged, or written into draft state.
- Requests are paced at just over one per second, bounded, cached, and backed off after failures or provider rate limits.
- A complete normalized base player catalog is cached for 24 hours; a provider-limited partial catalog is retried after five minutes. Injury and news snapshots are cached for five minutes. Fresh persistent snapshots survive a server restart and avoid both network requests and local request-budget reservations.
- When a snapshot expires, the provider attempts a normal paced and budgeted refresh. If that refresh fails, last-known-good data up to seven days old may still support identity matching, but recommendation risk remains unknown: stale status is not treated as current and stale headlines are not presented as recent news.
- This app reserves at most 95 requests per UTC day in a private persistent counter, leaving a margin below the public 100-request limit. Other programs using the same key can still consume the provider's account-wide allowance.
- Bounded player-detail lookups are used only when at least one requested ranking remains unresolved. Unrelated recent-news IDs do not consume that lookup allowance or produce an identity-coverage warning after the requested ranking pool is already resolved.
- Only allowlisted player identity, status, timestamp, category, and headline fields reach recommendations. Fresh, exactly resolved rows may still be used when the API labels its overall coverage as limited; missing, unresolved, stale, rate-limited, unavailable, or out-of-coverage players remain explicitly unknown. The UI describes these results as bounded snapshots rather than inferring an API plan, and distinguishes a working feed with no fresh injury match from an unavailable feed.

The first FantasyPros-enabled recommendation automatically fetches the base player catalog, injuries, and news and populates `~/.fantasy-football-mcp/fantasypros-snapshots.sqlite3` with each successful snapshot. No separate prefetch, database migration, or user refresh command is needed. The SQLite cache is bounded to sixteen snapshot variants and 8 MB of normalized record JSON. It contains normalized FantasyPros base snapshots only—not the API key, raw provider bodies, URLs, query strings, targeted identity lookups, Yahoo data, draft state, league IDs, or recommendation candidates. User-facing warnings identify stale fallback; stale per-player status and headlines are suppressed.

## Databricks advisory critic

The Databricks integration is an optional, advisory-only second look at a completed deterministic recommendation. The local deterministic engine remains authoritative: the model cannot reorder candidates, change scores, select or draft a player, or feed output back into any specialist. A missing, incomplete, or defective authoritative ledger skips the advisory path. Stale state may still produce degraded deterministic candidates; when it does, staleness is disclosed as a quality flag and the deterministic response remains visibly degraded. Disabled or skipped advisory work is omitted from the response entirely; a timeout, dependency problem, authentication failure, or invalid model response fails open and leaves the deterministic recommendations unchanged.

Install the public SDK dependencies, then configure a private `.env` with a generic workspace root and serving-endpoint name:

```bash
uv sync --extra databricks
```

```env
FANTASY_FOOTBALL_DATABRICKS_ADVISORY_ENABLED=true
FANTASY_FOOTBALL_DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
FANTASY_FOOTBALL_DATABRICKS_MODEL=your-serving-endpoint
FANTASY_FOOTBALL_DATABRICKS_ADVISORY_TIMEOUT_SECONDS=8.0
```

The host must be an HTTPS Databricks workspace root with no path, query string, embedded username, or credential. The model is the exact serving endpoint made available by that workspace. Configure one of the Databricks SDK's supported unified-authentication methods outside this app; this app does not accept, persist, return, or log a Databricks access token. Do not commit a company workspace hostname, endpoint name, profile, or token to this public repository. Restart the server after changing the configuration.

The eight-second default deadline includes first-call client/authentication setup as well as inference. You can configure a shorter deadline for a tighter draft clock; a timeout returns only an unavailable advisory notice and leaves the deterministic board unchanged.

Only the following allowlisted, identity-free summary can be sent to the configured Databricks endpoint, and only after the local ledger is complete:

- Candidate ordinal and position; overall and specialist component scores; explicitly uncalibrated return probability; and normalized risk status
- Aggregate roster position counts and the positions of a bounded number of recent picks
- Current and next overall-pick numbers
- Bounded quality flags for stale state, unavailable evidence, or inferred settings

Player names and keys, league/session/team identifiers, the pick ledger, news and headlines, URLs, browser fields, Yahoo credentials, and Databricks credentials are excluded. Provider output is treated as untrusted: only bounded summary/caution text is rendered, and it never changes candidate order. Identical sanitized inputs use a small, short-lived in-memory cache to reduce latency and requests; nothing from this advisory cache is written to disk, and restarting the server clears it. Any retention performed by the configured Databricks workspace is governed by that workspace's own policy.

## Yahoo API setup and limitations

Creating a [Yahoo developer app](https://developer.yahoo.com/apps/) and obtaining OAuth tokens is not enough by itself. Yahoo manually provisions Fantasy Sports API access through its [Fantasy API access process](https://sports.yahoo.com/developer/access/). Associate the approval request with the same client ID; otherwise Fantasy calls return `401` with `oauth_problem="additional_authorization_required"`.

That error is an entitlement problem:

- Re-running OAuth does not fix it.
- Refreshing the access token does not fix it.
- Use a local rankings profile for live draft recommendations while approval is pending.

Yahoo currently grants read-only Fantasy API access, so this project's tools inspect and recommend but do not transact. After approval, put the Yahoo client values in `.env` and run the private OAuth helper:

```bash
uv run python utils/setup_yahoo_auth.py
```

Run it in a private terminal and do not share its output. For an expired access token use `ff_refresh_token` or `uv run python utils/refresh_yahoo_token.py`; use `utils/reauth_yahoo.py` only when full reauthorization is required. Restart the server or MCP client after credentials change.

A normal Yahoo-backed MCP workflow is:

1. `ff_get_leagues` to discover, never guess, a `league_key`.
2. `ff_get_league_info` for settings and the authenticated team.
3. Use roster, matchup, standings, player, waiver, draft, or lineup-analysis tools with that exact key.

## MCP tools

The main FastMCP server exposes:

- `ff_get_leagues`, `ff_get_league_info`, `ff_get_standings`
- `ff_get_roster`, `ff_get_matchup`, `ff_get_players`, `ff_compare_teams`
- `ff_build_lineup`, `ff_get_waiver_wire`
- `ff_get_draft_results`, `ff_get_draft_rankings`, `ff_get_draft_recommendation`, `ff_analyze_draft_state`
- `ff_get_live_draft_state`, `ff_get_live_draft_recommendation`
- `ff_analyze_reddit_sentiment`
- `ff_refresh_token`, `ff_get_api_status`, `ff_clear_cache`

Reddit sentiment is optional; see [Reddit API setup](docs/REDDIT_API_SETUP.md) if you want to configure it.

Point a compatible HTTP MCP client at `http://127.0.0.1:8765/mcp`. Client configuration syntax varies. The legacy stdio entry point remains available for clients that launch a command directly:

```bash
uv run python fantasy_football_multi_league.py
```

The stdio process does not replace the port-8765 FastMCP process needed by the recorder, dashboard, and local recommendation routes.

## Troubleshooting

### Popup says agent sync is offline

- Confirm the port-8765 server is still running and bound to `127.0.0.1`.
- Reload the extension and the exact Yahoo draft tab, return to **Results → Round by Round**, then select **Rescan page**.
- Recording and Agent JSON export still work while server sync is offline, but the sidebar/dashboard cannot use a newer draft state.

### `Permission denied to access property "then"` in Firefox

This was associated with stale recorder code using Firefox's content-script Promise boundary. The current extension routes same-draft locking through its background broker and needs no extra website permission. In `about:debugging`, reload the temporary add-on; then close stale tabs for that mock, reload the exact active draft tab, show **Results → Round by Round**, and rescan. If it persists on the current checkout, save sanitized diagnostics from the popup before reporting it.

### “Yahoo league identity could not be resolved”

- First open the exact active draft tab and rescan so the popup and server agree on its numeric league ID.
- For an Instant Mock Draft or while Yahoo approval is pending, open the dashboard from that popup and import, explicitly bind, or configure a saved local profile as the Yahoo Football default. Then refresh recommendations; this path should not call Yahoo.
- For a real Yahoo league using fallback, confirm `ff_get_leagues` returns exactly one matching current-season league and that the authenticated Yahoo team matches the draft.
- Do not choose the newest saved session by guesswork or copy a league ID from a different mock.

### `additional_authorization_required`

The Yahoo app is not provisioned for Fantasy Sports. Apply for access using the same client ID or use local-profile draft mode. Token refresh is not a remedy.

### Ledger has gaps, duplicates, or phantom picks

Open the complete **Results → Round by Round** table and use **Full rescan & repair**. Do not use Reset unless you intend to start that exact mock over. Recommendations remain blocked until the authoritative ledger passes validation.

### Rankings import fails

- Open and sync the target draft before importing; profiles are league-bound.
- For a spreadsheet, download a local `.xlsx` or export CSV. There is no live Google Drive/Sheets connection.
- Verify CSV includes one unambiguous Rank/ECR, Player Name, and Position column; use supported NFL positions and unique ranks.
- Verify `.xlsx` is the supported DraftSheets layout and no larger than 2 MB.

## Local data and privacy

Private runtime data is stored under `~/.fantasy-football-mcp/`. The default directory is mode `0700`, and these files are mode `0600`. It may include:

- `live-drafts.json` — sanitized per-league draft sessions
- `draft-profiles.json` — sanitized rankings, roster settings, and profile provenance
- `draft-profile-defaults.json` — explicit per-sport pointers to saved default profiles
- `fantasypros-snapshots.sqlite3` — normalized FantasyPros base snapshots only
- `fantasypros-request-budget.json` — only UTC date and request count

The browser profile separately stores the extension's sanitized per-league recorder state. The recorder does not store Yahoo cookies, OAuth credentials, page URLs, query parameters, chat, or arbitrary page fields. Loopback routes validate origins, cap payloads, allowlist fields, and return recommendation responses with `Cache-Control: no-store`.

FantasyPros receives only its API requests. No draft ledger or Yahoo credential is sent to FantasyPros. When the Databricks advisory critic is explicitly enabled, Databricks receives only the identity-free allowlist described above; this app keeps its advisory cache in memory only. Google Drive receives nothing from this app because it is not a runtime integration.

## Development and validation

Contributors and coding agents should read [AGENTS.md](AGENTS.md). Prioritized follow-up work is tracked in [IMPROVEMENTS.md](IMPROVEMENTS.md).

Run the main checks with:

```bash
uv run --extra dev pytest -q
npm --prefix chrome-extension test
git diff --check
```

The deterministic recommendation engine is credential-free and independently testable. Yahoo network calls stay serialized in the service layer; specialist scorers make no network requests.

## Project structure

```text
fantasy-football-mcp-public/
├── fastmcp_server.py              # HTTP MCP server and private loopback routes
├── fantasy_football_multi_league.py
├── chrome-extension/              # Yahoo draft recorder and Firefox sidebar
├── src/dashboard/                 # Loopback-only full dashboard
├── src/services/                  # Draft state, profiles, recommendations, providers
├── src/agents/                    # Deterministic live-draft specialists
└── tests/
```

## License

See [LICENSE](LICENSE).
