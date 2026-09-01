# Yahoo Fantasy Draft Recorder

A Firefox- and Chrome-compatible Manifest V3 WebExtension that watches a Yahoo Fantasy Football live draft, records completed picks in local extension storage, syncs agent-ready context to the local MCP server, exports the draft as CSV or JSON, and presents live recommendations without changing the Yahoo page or drafting players.

## What it records

When Yahoo exposes the data in the draft row, each pick includes:

- Overall pick
- Round and pick within the round
- Player
- NFL position and team
- Fantasy team/manager
- Local recording timestamp

The extension scans the existing draft log when it loads and then watches DOM changes for new picks. Ordinary non-ledger observations are merged using the overall pick number. A coherent Yahoo **Results → Round by Round** table instead replaces the stored numbered ledger exactly, preserving missing and duplicate pick numbers so server recommendations remain blocked. Only conservatively unmatched unnumbered live observations remain alongside it; initialed names require matching position and NFL team. Later non-ledger scans preserve that authoritative numbered state while appending genuinely new pick numbers. Open Round by Round once to backfill every completed pick. The smaller `Last:` banner continues capturing new picks while other draft tabs are visible.

Rows labeled **Your Team** are marked as your picks and are highlighted in the popup.

## Install temporarily in Firefox

This does not require Chrome's **Load unpacked** feature:

1. Open `about:debugging` in Firefox.
2. Select **This Firefox**.
3. Click **Load Temporary Add-on…**.
4. Select `chrome-extension/manifest.json` from this repository.
5. Open or reload a Yahoo live draft under `https://football.fantasysports.yahoo.com/draftclient/...`.
6. Open **Results → Round by Round** once, then click the extension icon and select **Rescan page**.
7. Use the popup to review, export, repair, or reset the exact active mock draft.

## Draft Assistant sidebar and dashboard

Firefox exposes a persistent **Fantasy Draft Assistant** sidebar. Open the recorder popup and select **Open Draft Assistant**, or use Firefox's sidebar menu. The recorder popup remains the place for scan, repair, export, and exact-session mock-reset controls; the sidebar is a read-only recommendation surface that stays open beside Yahoo. Chrome users can select **Full dashboard** in the popup instead.

The sidebar selects a league only from the active Yahoo draft tab or an explicit league choice. It never silently falls back to the newest saved league. Select a strategy and use **Refresh recommendations** to request the top five options. Each card shows roster fit, rank/ADP/tier/bye context, reasoning, injury/news risk, an explicitly uncalibrated confidence, and explicitly uncalibrated heuristic/simulation probabilities. The panel also surfaces exact ledger blockers and prominently degrades stale state, inferred team counts, unresolved drafted identities, unavailable roster settings, and unknown injury/news data. It never injects UI into Yahoo and never selects or drafts a player.

For a wider board, open `http://127.0.0.1:8765/draft-dashboard` while the local server is running. The sidebar's dashboard link puts the explicitly selected league ID in a URL fragment, which browsers do not send in the HTTP request. The sidebar and dashboard share the same local client, response view-model, and safe text-only renderer; the dashboard may show up to twenty candidates without changing the recommendation contract.

While Yahoo Fantasy API approval is pending, use the dashboard's **Local draft
profile** form. It accepts DraftSheets 2026 `.xlsx`, an ECR CSV, or strict
`schemaVersion: 1` JSON and binds the normalized top 500 rankings plus roster slots to
the explicitly selected recorder session. For XLSX imports, the browser sends only raw
bytes (maximum 2 MB) and allowlisted team/roster headers to the loopback server—never
the filename—and the server discards the workbook after parsing its ECR/Scoring cells.
The imported profile supplies rankings and league settings with no Yahoo API call while
the extension continues supplying every live pick. Re-import it for the actual live
session if earlier testing used a Yahoo mock draft.

If `FANTASY_PROS_API` is configured on the local server, recommendation cards also show
allowlisted FantasyPros injury/news attribution and recent headlines. Provider calls
remain server-side; the extension never receives the key. Missing, unresolved, stale,
or API-tier-limited evidence is shown as unknown/limited rather than healthy.

## Mock-draft reset and ledger repair

These controls have different purposes:

1. Use **Reset mock draft** when starting a test over. Keep the local server running,
   open the exact Yahoo mock-draft tab, close older tabs for the same mock/session, and
   confirm Reset in the popup. Reset is disabled for a merely saved/latest draft.
2. The popup first reconciles any durable repair, records a durable reset intent, and
   asks the loopback server to reset only the active sport/league/team/session at the
   exact last-synced timestamp. A changed draft returns an error without deleting it;
   rescan and confirm Reset again.
3. After server acceptance, browser picks, the pending repair journal, and legacy data
   for only that session are cleared behind a server-timestamped tombstone. The separate
   imported DraftSheets profile is preserved. Interrupted reset cleanup resumes when
   the popup is reopened, while all scans and sync remain blocked behind its journal.
4. The popup waits until a fresh browser timestamp is strictly later than the server
   reset, then rescans the current Yahoo page. If the page cannot be rescanned, wait a
   moment and use **Rescan page**. A new/empty mock begins clean; an in-progress page
   correctly rebuilds from the picks Yahoo currently shows.
5. Use **Full rescan & repair** instead when you want to keep the current draft but fix
   gaps, duplicates, unnumbered observations, or a phantom high pick. Open the complete
   **Results → Round by Round** table before confirming repair.

If the popup reports a ledger problem, it lists the exact missing pick numbers, duplicate pick numbers, and sanitized details/count for unnumbered observations. Missing and duplicate numbers come from the coherent raw authoritative table and remain present in the saved and server-bound pick list; saved-session observations supply the unnumbered details. Conflicting or malformed Yahoo tables show an explicit recovery error instead of appearing healthy, and leaving the authoritative table clears its raw scan status without erasing saved defects. An automatic scan never replaces a saved ledger with a shorter visible prefix, even when Yahoo’s current-pick text makes that prefix look current; it retains and does not sync the saved state and directs the user to explicit repair. While the complete current **Results → Round by Round** ledger is visible, select **Full rescan & repair** and confirm the replacement. The recorder stages a replacement only when every nonempty result row has the expected three-cell Yahoo shape and parses safely, responsive table copies agree, the numbered result is contiguous and unique, and—when Yahoo exposes the current pick—the ledger ends immediately before it. A repair that would lower the saved maximum additionally requires that live current-pick evidence, so it is unavailable while Yahoo is paused or otherwise hides the current pick. The recorder then sends the staged context to the local server with an explicit repair marker and saves it in browser storage only after the server accepts it. Server rejection or unavailability leaves the exact saved browser session unchanged. Durable per-league repair and reset journals survive reloads and sibling tabs; if server acceptance or browser persistence is interrupted, the recorder blocks stale work and reconciles that journal before any further scan or ordinary sync. Same-league scans and repairs are serialized across Yahoo tabs, while independent per-league storage keys prevent another league from being overwritten. Reset uses the server's reset time for its exact-league tombstone and removes that league's pending repair state; any pre-reset scan or repair written afterward remains hidden/rejected, while a genuinely later rescan can begin again. Legacy aggregate cleanup is separately serialized in the extension context so concurrent league resets preserve unrelated drafts. Legacy aggregate sessions remain readable and migrate safely on their next update. Recommendations remain blocked until the authoritative ledger passes those checks.

After changing the code, use **Reload** for the add-on in `about:debugging`, then reload the Yahoo draft tab.

A temporary add-on is removed when Firefox restarts. Permanent installation in standard Firefox requires a Mozilla-signed package, either through an addons.mozilla.org listing or an unlisted AMO signing submission.

## Agent handoff

### Automatic local MCP sync

Start the FastMCP server on its default loopback port before opening the draft:

```bash
HOST=127.0.0.1 PORT=8765 python fastmcp_server.py
```

After each changed scan, the extension posts sanitized agent context to `http://127.0.0.1:8765/draft-sync`. An agent connected to this MCP server can call `ff_get_live_draft_state` to inspect the ledger or `ff_get_live_draft_recommendation` for an orchestrated primary pick, alternatives, confidence, risk, and contingency. Synced state is stored with user-only file permissions at `~/.fantasy-football-mcp/live-drafts.json`.

The sidebar posts only `schemaVersion`, the explicitly selected `leagueId`, and bounded strategy/count/ranking/simulation settings to `http://127.0.0.1:8765/draft-recommendation`. It does not send the pick ledger again, a Yahoo URL, credentials, cookies, auth parameters, arbitrary page fields, team IDs, or session keys. The server resolves the saved league-scoped state and exact local profile (or Yahoo fallback) independently, then rechecks both after scoring; if another pick synced or the profile changed during the request, every candidate is discarded and the UI asks for a refresh. Both recommendation surfaces use `Cache-Control: no-store` responses from the loopback-only endpoint.

The endpoint accepts writes only from the loopback interface and validates and whitelists every field. If the server is not running, recording still works and the popup reports that agent sync is offline.

### Manual handoff

Click **Agent JSON** to download a recommendation-ready file containing:

- The complete ordered pick ledger
- Your roster
- Rosters grouped by fantasy team
- Current and next overall-pick numbers
- Sanitized draft identifiers

CSV remains available for spreadsheet use.

## Privacy

- Pick history stays in the current browser profile and, when local sync is available, in the user-owned MCP state file on the same computer.
- No draft data is sent to Yahoo beyond normal page use, to the extension developer, or to any third party.
- The extension does not store or sync the draft URL, Yahoo cookies, the `auth` query parameter, or Yahoo credentials.
- Host access is limited to Yahoo's `/draftclient/` pages and loopback draft sync/reset/recommendation/dashboard routes.

## Yahoo layout changes

Yahoo can change its draft page without notice. Detection favors semantic attributes (`data-pick-number`, player/team labels, ARIA labels) and falls back to common draft-row text formats. **Save diagnostics** exports only structural counters and allowlisted field-presence counts; it excludes raw page text, CSS classes, test IDs, ARIA text, URLs, query strings, chat, and manager text. If the popup says rows were found but could not be parsed, compare those counters with a locally inspected completed-pick row so `dom-scanner.js` can be updated without sharing raw page content.

## Store preparation

`manifest.json` includes a stable Firefox add-on ID, Firefox 142 minimum version, and Firefox's required `none` data-collection declaration. `CHROMEWEBSTORE.md` tracks the single purpose, listing copy, permission justifications, data-use disclosures, remote-code declaration, and Chrome submission checklist. Keep these synchronized when behavior or permissions change.

## Tests

No package installation is required; the tests use Node's built-in test runner.

```bash
npm --prefix chrome-extension test
```

The tests cover URL sanitization, fixture-based Round-by-Round parsing and unexpected cell shapes, privacy-minimal diagnostics, authoritative duplicate/gap persistence into agent context, raw/saved ledger-health merging, exact ledger issue reporting, guarded and durable full repair/reset, cross-league storage interleaving, same-league tab serialization, reload reconciliation, duplicate merging, local session updates, loopback sync/reset/recommendation requests, explicit league selection, bounded recommendation view-models, inert text-only rendering, DOM snapshotting, Firefox/Chrome API compatibility, and CSV/JSON export safety.
