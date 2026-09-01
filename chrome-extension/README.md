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

The extension scans the existing draft log when it loads and then watches DOM changes for new picks. Duplicate observations are merged using the overall pick number. Yahoo's **Results → Round by Round** table provides the authoritative all-team ledger; open that view once to backfill every completed pick. The smaller `Last:` banner continues capturing new picks while other draft tabs are visible.

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

Yahoo can change its draft page without notice. Detection favors semantic attributes (`data-pick-number`, player/team labels, ARIA labels) and falls back to common draft-row text formats. If the popup says rows were found but could not be parsed, capture the HTML for one completed-pick row from DevTools so its selector can be added to `dom-scanner.js`.

## Store preparation

`manifest.json` includes a stable Firefox add-on ID, Firefox 142 minimum version, and Firefox's required `none` data-collection declaration. `CHROMEWEBSTORE.md` tracks the single purpose, listing copy, permission justifications, data-use disclosures, remote-code declaration, and Chrome submission checklist. Keep these synchronized when behavior or permissions change.

## Tests

No package installation is required; the tests use Node's built-in test runner.

```bash
npm --prefix chrome-extension test
```

The tests cover URL sanitization, Round-by-Round parsing, duplicate merging, local session updates, agent-context creation, loopback sync requests, DOM snapshotting, Firefox/Chrome API compatibility, and CSV/JSON export safety.
