# Yahoo Fantasy Draft Recorder

A Firefox- and Chrome-compatible Manifest V3 WebExtension that watches a Yahoo Fantasy Football live draft, records completed picks in local extension storage, syncs agent-ready context to the local MCP server, and exports the draft as CSV or JSON.

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
7. Use the popup to review, export, or clear recorded picks.

If the popup reports a ledger problem, it lists the exact missing pick numbers, duplicate pick numbers, and sanitized details/count for unnumbered observations. Missing and duplicate numbers come from the coherent raw authoritative table and remain present in the saved and server-bound pick list; saved-session observations supply the unnumbered details. Conflicting or malformed Yahoo tables show an explicit recovery error instead of appearing healthy, and leaving the authoritative table clears its raw scan status without erasing saved defects. An automatic scan never replaces a saved ledger with a shorter visible prefix, even when Yahoo’s current-pick text makes that prefix look current; it retains and does not sync the saved state and directs the user to explicit repair. While the complete current **Results → Round by Round** ledger is visible, select **Full rescan & repair** and confirm the replacement. The recorder stages a replacement only when every nonempty result row has the expected three-cell Yahoo shape and parses safely, responsive table copies agree, the numbered result is contiguous and unique, and—when Yahoo exposes the current pick—the ledger ends immediately before it. A repair that would lower the saved maximum additionally requires that live current-pick evidence, so it is unavailable while Yahoo is paused or otherwise hides the current pick. The recorder then sends the staged context to the local server with an explicit repair marker and saves it in browser storage only after the server accepts it. Server rejection or unavailability leaves the exact saved browser session unchanged. A durable per-league repair journal survives reloads and sibling tabs; if server acceptance or browser persistence is interrupted, the recorder blocks stale work and reconciles that journal before any further scan or ordinary sync. Same-league scans and repairs are serialized across Yahoo tabs, while independent per-league storage keys prevent another league from being overwritten. Clear atomically records a timestamped league tombstone and removes its pending repair journal; a pre-Clear scan or repair that writes afterward remains hidden, while a genuinely later scan can resume recording. Legacy aggregate cleanup is separately serialized in the extension context so concurrent league clears preserve unrelated drafts. Legacy aggregate sessions remain readable and migrate safely on their next update. Recommendations remain blocked until the authoritative ledger passes those checks.

After changing the code, use **Reload** for the add-on in `about:debugging`, then reload the Yahoo draft tab.

A temporary add-on is removed when Firefox restarts. Permanent installation in standard Firefox requires a Mozilla-signed package, either through an addons.mozilla.org listing or an unlisted AMO signing submission.

## Agent handoff

### Automatic local MCP sync

Start the FastMCP server on its default loopback port before opening the draft:

```bash
HOST=127.0.0.1 PORT=8765 python fastmcp_server.py
```

After each changed scan, the extension posts sanitized agent context to `http://127.0.0.1:8765/draft-sync`. An agent connected to this MCP server can call `ff_get_live_draft_state` to inspect the ledger or `ff_get_live_draft_recommendation` for an orchestrated primary pick, alternatives, confidence, risk, and contingency. Synced state is stored with user-only file permissions at `~/.fantasy-football-mcp/live-drafts.json`.

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
- Host access is limited to Yahoo's `/draftclient/` pages and the loopback sync endpoint.

## Yahoo layout changes

Yahoo can change its draft page without notice. Detection favors semantic attributes (`data-pick-number`, player/team labels, ARIA labels) and falls back to common draft-row text formats. **Save diagnostics** exports only structural counters and allowlisted field-presence counts; it excludes raw page text, CSS classes, test IDs, ARIA text, URLs, query strings, chat, and manager text. If the popup says rows were found but could not be parsed, compare those counters with a locally inspected completed-pick row so `dom-scanner.js` can be updated without sharing raw page content.

## Store preparation

`manifest.json` includes a stable Firefox add-on ID, Firefox 142 minimum version, and Firefox's required `none` data-collection declaration. `CHROMEWEBSTORE.md` tracks the single purpose, listing copy, permission justifications, data-use disclosures, remote-code declaration, and Chrome submission checklist. Keep these synchronized when behavior or permissions change.

## Tests

No package installation is required; the tests use Node's built-in test runner.

```bash
npm --prefix chrome-extension test
```

The tests cover URL sanitization, fixture-based Round-by-Round parsing and unexpected cell shapes, privacy-minimal diagnostics, authoritative duplicate/gap persistence into agent context, raw/saved ledger-health merging, exact ledger issue reporting, guarded and durable full repair, cross-league storage interleaving, same-league tab serialization, reload reconciliation, duplicate merging, local session updates, loopback sync requests, DOM snapshotting, Firefox/Chrome API compatibility, and CSV/JSON export safety.
