# Fantasy Football MCP Server

A private, single-user Yahoo Fantasy Football MCP server with a Firefox/Chrome draft recorder and local recommendation UI. Use it for ordinary league research through Yahoo, or run live draft recommendations from the browser recorder and a local rankings profile without Yahoo Fantasy API access.

The assistant is recommendation-only. It does not set a lineup, add or drop a player, or make a draft pick for you.

## Two ways to use it

- **Live draft assistant:** the extension records Yahoo's numbered draft ledger and an extracted Yahoo player key when the page exposes one, the local server combines it with imported rankings and roster settings, and the sidebar/dashboard recommends the next pick. Yahoo API approval is not required when the current draft has a local profile.
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

For API-free live draft recommendations, the Yahoo variables may remain unset. Optional FantasyPros injury/news/projection enrichment needs only:

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
8. Open the Firefox **Draft Assistant** sidebar or the full dashboard. Live recommendation refresh starts automatically; **Refresh recommendations** remains available for an immediate manual run.

The recorder continues watching new picks when other Yahoo draft panels are visible. DOM bursts settle behind a 400 ms quiet window, but continuous Yahoo updates cannot postpone a scan beyond about one second; a change that arrives during an active scan produces one serialized follow-up scan. In particular, the active Yahoo **Picks** tab is a strict secondary feed for its currently rendered cards, alongside the last-pick banner; **Queue** entries are never treated as drafted players. The recorder does not scroll that virtualized panel; it accumulates newly rendered cards through Yahoo DOM changes and manual rescans. Return to **Results → Round by Round** and rescan whenever the saved ledger is questionable because that complete numbered table remains the only authoritative source and the only repair input. Recommendations are deliberately blocked for missing, duplicate, or unnumbered picks, and whenever the recorder cannot verify authoritative ledger capture integrity.

### 6. Import rankings and league settings

The dashboard's **Local draft profile** section accepts:

- A supported DraftSheets 2026 `.xlsx` workbook, up to 2 MB; reception scoring of 0, 0.5, or 1 is retained when present
- An ECR `.csv` with required rank/ECR, player-name, and position columns; team, ADP, bye, and Yahoo player key are optional
- A strict `schemaVersion: 1` `.json` profile

Before importing, check the team count and roster slots shown in the form. The server keeps at most 500 sanitized ranking rows and binds them to the exact recorder identity. It does not store the raw workbook, filename, formulas, notes, URLs, or arbitrary cells.

Google Drive is optional storage only; this app does not read Drive or Google Sheets at runtime. Download the sheet to your computer as `.xlsx` or export its ECR tab as CSV, then choose that local file in the dashboard.

A matching local profile provides rankings and league settings with zero Yahoo API calls. Drafted players still come from the extension's live ledger. When both a ledger pick and ranking carry the same validated numeric Yahoo `player_key`, that key is preferred; unequal keys never fall back to a same-name match. If either side lacks a key, Yahoo's initialed player names are matched conservatively by name, position, and canonical NFL team. Known equivalent provider codes such as Jacksonville's `JAX` and `JAC` are normalized before availability is decided.

#### Optional Breakout Watch evidence

Generic CSV and strict JSON profiles may include explicit projection evidence for RB, WR, and TE players. A CSV row must either omit all six optional columns or provide all of them: `Projection Source`, `Projection As Of` (or `Projection Date`), `Projected Points`, `Projected Opportunities`, `Opportunity Kind`, and `Experience Years`. JSON uses the equivalent complete `breakout_evidence` object with `source`, `as_of`, `projected_points`, `projected_opportunities`, `opportunity_kind`, and `experience_years`. RB opportunity kind must be `touches`; WR and TE may use `targets` or `receptions`. Projected receptions are never relabeled as targets.

The source is a short attribution label, not a URL, and the evidence date must be an ISO date in the profile season. At recommendation time the evidence must be no more than 45 days old, with at most one day of future clock skew. A **Breakout Watch** label requires a same-position, same-source, same-opportunity-kind cohort of at least five players, no more than three years of experience, and both projected points and opportunities at or above that cohort's 60th percentile. The cohort comes from the full bounded ranking snapshot, including already drafted players, so advancing the ledger does not by itself change the classification.

Breakout Watch is an explicitly uncalibrated screening heuristic, not a projection of NFL success. It is never inferred from ADP, rank movement, or news sentiment. Missing or incomplete evidence simply omits the label and explains that breakout evidence is unavailable; it does not degrade otherwise valid recommendations. Current DraftSheets `.xlsx` imports do not carry these optional fields. When FantasyPros projection evidence is available, the service conservatively joins it to `years_exp` from Sleeper's NFL player catalog. A unique Yahoo ID plus position is authoritative. Without one, the fallback requires a unique exact name, position, and canonical NFL team; initialed names stop at that strict tier. Non-initialed names may additionally match after removing one terminal generational suffix while retaining exact position/team, or when an exact name/position has only the `FA`-versus-missing-team difference. Conflicting IDs, nonempty team conflicts, ambiguous rows, and unmatched stable IDs never fall back loosely. Complete matches can therefore create `FantasyPros + Sleeper` Breakout Watch cohorts automatically, while unresolved identities remain unavailable.

### 7. Get recommendations

The easiest interfaces are:

- **Extension popup:** recorder status, ledger health, rescan/repair/reset controls, CSV export, and Agent JSON export. It does not show or make draft picks.
- **Firefox Draft Assistant:** a persistent top-five recommendation sidebar next to Yahoo. Its at-a-glance decision brief shows whether you are on the clock, next, or a known number of picks away, followed by the primary recommendation and two immediate fallbacks. A bounded **Next two selections** section adds position-aware next-turn combinations when snake order is known. Candidate cards can show inert, attributed FantasyPros projection evidence separately from a qualifying evidence-backed Breakout Watch label. An exact-session personal queue stays in extension storage and can optionally send deduplicated browser notifications only when the authoritative ledger says you are next or on the clock. New revisions for the exact selected session automatically replace stale work; sync bookkeeping with the same revision does not cancel a valid request. Press plain **R** outside a form control to refresh manually. Chrome users should use the dashboard.
- **Full dashboard:** the same clock-aware decision brief and two-selection plan plus a live draft cockpit: personal queue, position/tier board, conservative/balanced/aggressive sensitivity, position-run alerts, configured roster-slot gaps, fallback tiers, pre-draft readiness checks, two-to-three player comparison, and a value/reach recap. **Live refresh** is enabled by default. The visible page polls a privacy-minimal local revision containing no picks or player/team identities, waits briefly for fast pick bursts, and refreshes only the newest exact league revision. Existing cards remain visible while scoring; hidden tabs pause, transient errors back off, and action-required errors pause for that revision until a profile/settings/manual change or the next pick. The toggle can stop polling immediately. It also retains up to twenty detailed candidates, recent picks, specialist scores, critic checks, simulations, and source diagnostics.

Both views show the same transparent market-decision signals:

- **Value** means the current pick is at least one league round later than the player's explicitly supplied market ADP.
- **Sleeper Watch** means that real ADP is in Round 7 or later and the ranking source places the player at least one league round ahead of that ADP. It is a rank-versus-market discount, not a breakout or performance prediction.
- **Fade** means source rank trails real ADP by at least one league round. It is a caution, not a command to avoid the player.
- **Take now** means the uncalibrated ADP heuristic estimates less than a 50% chance that the player returns at your next pick; **Can wait** means 50% or more. The card always retains the raw rank, ADP, and estimated-return values behind that label.

These labels require real ADP, a same-season source date (or a same-season provider retrieval date), a complete authoritative ledger, and conservative resolution of every drafted identity. ADP may come from the imported ranking source or fresh, scoring-specific FantasyPros evidence, but the two sources are never mixed. A local import timestamp alone is diagnostic and does not prove that an undated sheet's ADP is current. Rank may still be used as an internal scoring fallback when ADP is absent, but it is never presented as market ADP and no timing badge is invented. Sleeper Watch shows at most five deterministically ordered players from the supplied ranking frontier (up to 500 rows), reports known drafted/unresolved/no-ADP/hidden counts, and marks counts unknown when the ledger cannot support them. A risk caution appears only for fresh attributed injury or structured-news evidence; missing or stale evidence remains unknown.

An MCP client can use the same state explicitly. The safe order is:

1. Call `ff_get_live_draft_state` with the popup's `league_id` and inspect its ledger/identity warnings.
2. Only when that state is ready, call `ff_get_live_draft_recommendation` with the same `league_id`.

When using Yahoo fallback instead of a local profile, a validated Yahoo `league_key` may also be supplied. The response labels whether market metadata came from the imported source date, import time, or a fresh Yahoo retrieval. Confidence, opponent-return probability, scenario probability, market timing, and Sleeper Watch are uncalibrated heuristics; injury/news evidence that is missing or stale remains **unknown**, never healthy.

The two-selection planner never changes the deterministic primary recommendation. It considers at most twelve of the top twenty scored candidates and returns at most two immediate fallbacks and three position-aware combinations. Next-turn availability is an uncalibrated actual-ADP heuristic. Missing actual ADP makes that availability unknown; stale state, inferred team count, unresolved drafted identities, or missing/inconsistent snake order visibly degrades the plan. Opponent-specific rosters and tendencies are not yet modeled, so an otherwise complete combination plan is also labeled degraded rather than presented as certain. An incomplete ledger blocks it, and unknown order omits future combinations instead of guessing. Keep live refresh enabled, or manually re-run recommendations after every recorded pick—this is a bounded scenario plan, not a promise that the second player will remain available.

## Instant Mock Drafts and repeated mocks

Yahoo Instant Mock Drafts use the same recorder flow. Scan the exact mock first, then import a local profile, explicitly bind a saved one, or configure a per-sport default before requesting advice. If an Instant Mock Draft identity is not discoverable through Yahoo's Fantasy API, this local-profile path is the expected recovery; refreshing an OAuth token does not make that mock a normal Yahoo league.

Each newly created Yahoo mock has a new identity. To reuse the same rankings without uploading them again:

1. Open and scan the new mock.
2. Open **Full dashboard** from its popup.
3. Under **Reuse a saved profile**, choose the prior source yourself.
4. Select **Use for this draft & refresh**.

Only sanitized rankings, roster settings, and provenance are copied. Picks are never copied between mocks.

Queue preferences are isolated by the new mock's exact Yahoo session identity (sport plus league ID). They are never copied from the source profile, so a repeated mock starts with an empty queue unless you add players for that exact mock.

To remove that manual step for later mocks, use **Default for future drafts** in the dashboard. Choose **Yahoo Football**, select the saved source, and select **Set sport default**. On the first recommendation for a newly synced draft with no exact profile, the server atomically binds that default to the new identity and continues without a Yahoo API call. An already bound or imported exact profile always wins, and changing or clearing the default does not alter prior drafts. Default selection and automatic binding require a source profile for the current UTC year, so import a new sheet and replace or clear the pointer after a season rollover.

Yahoo's draft-client URL does not reliably distinguish a mock from a real draft, and the recorder intentionally discards its query string. The default therefore applies to every future profileless recorder draft for that sport, including real drafts and mocks. The dashboard states this scope before you enable it; use manual binding instead if you draft in multiple leagues with different settings.

Use the two popup recovery controls for different jobs:

- **Reset mock draft:** start over within the same exact mock/session. Keep the server running, close older tabs for that same mock, open the active tab, rescan, and confirm Reset. The imported profile is preserved.
- **Full rescan & repair:** keep the current draft but replace a defective saved ledger. First show the complete current **Results → Round by Round** table. Repair is accepted only when the completed ledger is coherent, contiguous, unique, and current. Yahoo's pre-rendered unfilled future rows are excluded only when they have an unambiguous positive pick number at or beyond one stable, visible, non-conflicting live current-pick marker; malformed, earlier, or unverifiable rows still block repair and authoritative replacement.

An unsafe, ambiguous, or truncated authoritative scan preserves the prior numbered picks and syncs only a private boolean capture blocker for that exact league. The local recommender then stays blocked even if the older ledger looks contiguous. Numbered Picks-tab or banner observations captured before a verified Round-by-Round baseline are retained for recovery but also set this blocker. Only a later coherent Round-by-Round scan with a stable positive current-pick marker matching the ledger maximum, or a server-accepted repair, clears it. A coherent scan without current-pick evidence may update picks but preserves the prior capture decision; ordinary scans with no authoritative table cannot clear it. After a baseline exists, matching secondary observations deduplicate conservatively, while a different player reported for an already saved overall pick is retained as a duplicate. A secondary observation that later fills an already recorded gap is retained but also sets the capture blocker, so it cannot make the ledger recommendation-ready without Round-by-Round verification or repair.

If the server says the draft changed during reset, rescan and confirm Reset again. Neither operation can target a merely “latest” draft; the exact active identity is required.

## FantasyPros evidence cache

Set `FANTASY_PROS_API` to enable attributed NFL injury status, recent news, and preseason RB/WR/TE projection evidence from the [FantasyPros public API](https://api.fantasypros.com/public/v2/docs). Restart the server after adding or replacing the key.

FantasyPros' [public API terms](https://api.fantasypros.com/public/v2/terms-of-use) instruct clients to cache data rather than poll unnecessarily and require FantasyPros attribution when publishing analysis based on it. This app labels the provider as FantasyPros and uses a private local cache to limit network traffic.

The integration is defensive by design:

- The key is sent only as FantasyPros' `x-api-key` header and is never returned to the browser, logged, or written into draft state.
- Requests are paced at just over one per second, bounded, cached, and backed off after failures or provider rate limits. The service caps the entire FantasyPros enrichment phase at 10 seconds so a cold or stalled provider cannot consume the extension's 30-second recommendation deadline.
- A complete normalized base player catalog is cached for 24 hours; a provider-limited partial catalog is retried after five minutes. Injury and news snapshots are cached for five minutes. Fresh persistent snapshots survive a server restart and avoid both network requests and local request-budget reservations.
- Preseason projections request `week=0`, the explicit `RB:WR:TE` position set, and an explicit `STD`, `HALF`, or `PPR` scoring code. RB opportunity is projected rushing attempts plus receptions; WR/TE opportunity is projected receptions. Projection snapshots are cached for 24 hours, and absent/malformed values stay unavailable rather than becoming zero. Structured evidence reports returned/reported counts, provider limits, and the public limited-coverage flag.
- An exact `leagueSettings.scoringFormat` or supported reception-points value selects projection scoring. If reception scoring is unavailable, projections explicitly default to `HALF` and report that provenance. DraftSheets imports now retain `Receptions` values of `0`, `0.5`, or `1` as `STD`, `HALF`, or `PPR`. Projection requests use the validated profile/Yahoo league season; an absent or invalid season falls back to the current UTC year with an explicit warning and provenance.
- Same-season catalog `rank_adp` is used only for known `STD`, and `rank_adp_ppr` only for known `PPR`. For `HALF`, the service requests FantasyPros consensus rankings with `type=ADP`, `scoring=HALF`, `position=ALL`, and `week=0`, then retains exact player identity, `rank_ave`, and an optional validated numeric `player_yahoo_id`. That ID is used only for the cross-provider Sleeper join and is removed before scoring. The service never interpolates STD/PPR values or substitutes ECR for ADP.
- FantasyPros projections do not provide trustworthy career experience. The service fills that field only when the cached Sleeper catalog produces a conservative identity match. Projection and experience enrichment remain evidence-only and do not alter deterministic recommendation scores.

- When a snapshot expires, the provider attempts a normal paced and budgeted refresh. If that refresh fails, last-known-good data up to seven days old may still support identity matching, but recommendation risk remains unknown: stale status is not treated as current and stale headlines are not presented as recent news.
- This app reserves at most 95 requests per UTC day in a private persistent counter, leaving a margin below the public 100-request limit. Other programs using the same key can still consume the provider's account-wide allowance.
- Bounded player-detail lookups are used only when at least one requested ranking remains unresolved. Unrelated recent-news IDs do not consume that lookup allowance or produce an identity-coverage warning after the requested ranking pool is already resolved.
- Only allowlisted player identity, status, timestamp, category, and headline fields reach recommendations. Fresh, exactly resolved rows may still be used when the API labels its overall coverage as limited; missing, unresolved, stale, rate-limited, unavailable, or out-of-coverage players remain explicitly unknown. The UI describes these results as bounded snapshots rather than inferring an API plan, and distinguishes a working feed with no fresh injury match from an unavailable feed.

The first FantasyPros-enabled recommendation automatically fetches the base player catalog, injuries, news, one scoring-specific projection snapshot, and—when needed—the scoring-specific ADP snapshot, then populates the shared provider cache at `~/.fantasy-football-mcp/provider-snapshots.sqlite3`. FantasyPros and Sleeper snapshots are cached for 24 hours; injury/news snapshots retry after five minutes. On first use, an existing `fantasypros-snapshots.sqlite3` is copied atomically into the provider-neutral path and its schema is migrated transactionally while preserving valid rows; the legacy file is retained as a recovery copy. No separate command is needed. The SQLite cache is bounded to sixteen snapshot variants and 8 MB of normalized record JSON. It contains normalized allowlisted evidence and optional canonical decimal-string player IDs only—not the API key, raw provider bodies, URLs, query strings, targeted identity lookups, Yahoo credentials, league/team/session IDs, draft state, or recommendation candidates. A still-fresh HALF ADP snapshot written by an older version remains valid and gains optional Yahoo IDs at its next normal refresh; the bounded name/position/team tiers work immediately. User-facing warnings identify stale fallback; stale injury status/headlines remain suppressed and stale projection/ADP provenance remains explicit.

## Sleeper player experience cache

No Sleeper API key is required. When fresh FantasyPros preseason projections are present, the service requests Sleeper's `https://api.sleeper.app/v1/players/nfl` catalog endpoint and locally retains only RB/WR/TE identity fields, an optional canonical decimal-string Yahoo player ID, and `years_exp`; it does not treat the undocumented `active=true` query hint as proof that the returned snapshot is complete. The normalized catalog is cached for 24 hours in `~/.fantasy-football-mcp/provider-snapshots.sqlite3`. Network ingestion is capped at 16 MiB and 12,000 source records; the allowlisted normalized catalog remains capped at 10,000 records. Exceeding any of those bounds fails closed. The shared database keeps each provider snapshot under a separate allowlisted key. An existing `sleeper-players.json` is validated, inserted into SQLite on first use, and removed only after that write succeeds. The directory and database are restricted to `0700` and `0600` respectively. A failed refresh may use a cache no older than 45 days; older, malformed, oversized, or ambiguous data fails closed. The raw Sleeper response, unrelated player fields, and request URLs are not persisted. Recommendation diagnostics expose bounded counts by match tier for eligible RB/WR/TE ranking rows, never raw provider rows or unresolved player details.

Warm the cache without running a recommendation:

```bash
uv run python cache_sleeper_players.py
```

The command respects the 24-hour TTL and prints only bounded cache metadata as JSON. Use `--force` for an explicit network refresh. It exits nonzero only when no usable fresh or bounded-stale catalog is available. A daily cron entry can invoke the same command from the repository checkout; use absolute paths for both the checkout and `uv` because cron usually has a minimal environment.

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
- For an Instant Mock Draft or while Yahoo approval is pending, open the dashboard from that popup and import, explicitly bind, or configure a saved local profile as the Yahoo Football default. A successful exact-profile import or reuse refreshes recommendations automatically; this path should not call Yahoo.
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
- `provider-snapshots.sqlite3` — normalized FantasyPros catalog/injury/news/projection snapshots and Sleeper identity/experience snapshots
- `fantasypros-request-budget.json` — only UTC date and request count

The browser profile separately stores the extension's sanitized per-league recorder state. It may include an extracted numeric Yahoo player key such as `461.p.33536`; the containing link is used only during scanning and is never retained. The recorder does not store Yahoo cookies, OAuth credentials, page URLs, query parameters, chat, or arbitrary page fields. Loopback routes validate origins, cap payloads, allowlist fields, and return recommendation responses with `Cache-Control: no-store`.

FantasyPros receives only its API requests. No draft ledger or Yahoo credential is sent to FantasyPros. Sleeper receives only one generic NFL-player catalog request; no ranking list, league identity, or draft state is sent. When the Databricks advisory critic is explicitly enabled, Databricks receives only the identity-free allowlist described above; this app keeps its advisory cache in memory only. Google Drive receives nothing from this app because it is not a runtime integration.

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
