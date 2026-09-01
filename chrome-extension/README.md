# Yahoo Fantasy Draft Recorder

A Firefox- and Chrome-compatible Manifest V3 extension that records completed Yahoo Fantasy Football picks in the current browser profile, syncs sanitized context to the private local MCP server, exports CSV/JSON, and presents local recommendations. It does not change the Yahoo page or select a player for you.

For the complete server, `.env`, Yahoo API, and FantasyPros setup, start with the [main usage guide](../README.md).

## Before loading the extension

Install the project dependencies and start the loopback server from the repository root:

```bash
HOST=127.0.0.1 PORT=8765 uv run python fastmcp_server.py
```

Leave it running throughout the draft. Always use `127.0.0.1` for desktop use; the MCP transport has no local authentication boundary. Restart this process whenever `.env` credentials or API keys change.

The recorder still saves picks and can export Agent JSON when the server is offline, but recommendations and server-side reset/repair synchronization need this process.

## Install in Firefox

Firefox 142 or newer is required.

1. Open `about:debugging`.
2. Select **This Firefox**.
3. Select **Load Temporary Add-on…**.
4. Choose this directory's `manifest.json`.
5. Open or reload the exact Yahoo draft tab.

Use **Reload** in `about:debugging` after extension code changes, then reload the Yahoo tab. A temporary add-on disappears when Firefox restarts. Permanent installation in standard Firefox requires a Mozilla-signed package.

## Install in Chrome

Chrome 121 or newer is required.

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Choose the `chrome-extension` directory.
5. Open or reload the exact Yahoo draft tab.

Use **Reload** on the extension card after code changes, then reload the Yahoo tab.

## Draft-day workflow

Follow this order for each live draft or Yahoo mock:

1. Start the local server.
2. Load/reload the extension and the exact active draft tab.
3. In Yahoo, open **Results → Round by Round**.
4. Open the recorder popup and select **Rescan page**.
5. Check that the popup shows the expected league and pick count, no ledger defects, and **agent sync connected**.
6. Select **Full dashboard** from this popup so it opens with the exact selected league.
7. Import a local rankings profile or explicitly reuse one already saved for another mock.
8. Open Firefox's **Draft Assistant** sidebar or remain in the dashboard, then refresh recommendations.
9. When diagnosing any recommendation, inspect live draft state before requesting a next pick.

The smaller Yahoo last-pick banner continues supplying new observations while other draft panels are visible. Return to **Results → Round by Round** and rescan when anything looks incomplete; its numbered table is the authoritative ledger.

## What is recorded

When Yahoo exposes each field, a pick contains:

- Overall pick number
- Round and pick within the round
- Player name
- NFL position and team
- Fantasy team/manager label
- Whether Yahoo labeled it **Your Team**
- Local recording timestamp

The extension records only sanitized draft context. It does not record Yahoo credentials, cookies, chat, arbitrary page text, or the draft's page location.

## Popup, sidebar, and dashboard

### Recorder popup

The popup is the operational control surface:

- **Rescan page** asks the active Yahoo tab for current picks and forces loopback sync.
- **Full rescan & repair** safely replaces a defective saved ledger from the complete visible Round-by-Round table.
- **Save diagnostics** downloads privacy-minimal structural counters for Yahoo layout troubleshooting.
- **Export CSV** downloads the picks for spreadsheet use.
- **Agent JSON** downloads recommendation-ready state for a manual agent handoff.
- **Reset mock draft** clears only the exact active mock/session while preserving its imported profile.
- **Open Draft Assistant** opens Firefox's persistent recommendation sidebar.
- **Full dashboard** opens the wider local web UI; Chrome users use this instead of the Firefox sidebar.

The popup displays picks and recorder health, not recommendations, and it cannot make a draft selection.

### Firefox Draft Assistant

The sidebar shows up to five recommendations beside Yahoo. It selects a league only from the active draft tab or an explicit saved-league choice; it never silently chooses the newest saved session. After the initial request, a newer pick for that exact league cancels stale work, debounces duplicate events, and refreshes automatically. Another league's storage updates do not affect it.

Cards show roster fit, rank/ADP/tier/bye context, reasoning, injury/news risk, and explicitly uncalibrated confidence and return/simulation probabilities. Stale state, inferred team counts, unresolved player identities, unavailable roster settings, and unknown injury/news data are visibly degraded.

### Full dashboard

Open it from the popup or visit `http://127.0.0.1:8765/draft-dashboard` while the server is running. Opening it from the popup carries the exact league ID in a browser fragment, which is not sent in the initial dashboard GET.

The dashboard can show up to twenty candidates, roster construction, recent draft history, specialist comparisons, critic checks, simulations, and source/quality diagnostics. It and the sidebar share the same safe text-only renderer and never inject controls into Yahoo.

## Import a local rankings profile

Importing a profile is the recommended live-draft path when Yahoo has not approved Fantasy API access or when an Instant Mock Draft cannot be resolved as a normal league.

1. Open and scan the target draft first.
2. Open **Full dashboard** from its popup.
3. Confirm the league ID, team count, and roster slots.
4. Choose one supported local file:
   - DraftSheets 2026 `.xlsx`, up to 2 MB
   - ECR `.csv` with Rank/ECR, Player Name, and Position; Team, ADP, and Bye are optional
   - Strict `schemaVersion: 1` `.json`
5. Select **Import profile**, then refresh recommendations.

The server keeps only the top 500 sanitized rows, roster settings, and safe provenance for that exact sport/league/team/session identity. Raw workbook bytes are parsed in memory and discarded. Filenames, formulas, notes, URLs, and arbitrary cells are not stored.

Google Drive is optional storage only, not a runtime integration. Download a local `.xlsx` from Drive or export the ECR tab from Google Sheets as CSV, then import that file through the dashboard.

With an exact local profile, recommendations use the imported rankings and settings and make zero Yahoo API calls. The extension remains the source for every drafted player.

## Instant Mock Drafts and profile reuse

Yahoo Instant Mock Drafts use the normal scan/sync flow. Scan first and bind a local profile before requesting advice. If the dashboard says the Yahoo league identity could not be resolved, use the local-profile path; refreshing OAuth is not a fix for a mock that Yahoo's Fantasy API does not list as a league.

Each newly created mock has a new identity. To reuse rankings without uploading them again:

1. Open and scan the new mock.
2. Open **Full dashboard** from the new mock's popup.
3. Under **Reuse a saved profile**, explicitly choose the prior source.
4. Select **Use for this mock & refresh**.

No source is chosen automatically. The server validates the sport and copies only sanitized rankings, roster settings, and provenance. It never copies or merges either mock's picks.

## Reset versus ledger repair

Use these controls for different outcomes.

### Reset mock draft

Use Reset when starting the same exact mock over:

1. Keep the server running and close older tabs for that same mock/session.
2. Open the exact active mock tab and rescan it.
3. Select **Reset mock draft** and confirm the destructive action.
4. Wait for the popup to rescan after the server's reset timestamp.

The reset clears only that exact browser/server draft session and preserves its separate imported profile. If the server reports that the draft changed, no deletion occurs; rescan and confirm Reset again. Reset is disabled when the popup can show only a saved/latest draft instead of the exact active identity.

### Full rescan & repair

Use Repair when keeping the current draft but fixing missing, duplicate, unnumbered, or phantom high picks:

1. In Yahoo, show the complete current **Results → Round by Round** table.
2. Open the popup and select **Full rescan & repair**.
3. Confirm that you intend to replace the saved picks from that authoritative table.

Repair proceeds only when every nonempty row has the expected safe shape, responsive copies agree, numbered picks are contiguous and unique, and the ledger is current. Lowering a saved maximum additionally requires Yahoo's current-pick evidence. The server must accept the staged replacement before the browser writes it; failure leaves the existing exact session unchanged.

Ordinary automatic scans never replace a saved authoritative ledger with a shorter visible prefix. Recommendations stay blocked until ledger defects are repaired.

## FantasyPros injury/news evidence

When `FANTASY_PROS_API` is set on the server, recommendation cards can show FantasyPros-attributed status and recent headlines. The extension never receives the key.

The first FantasyPros-enabled recommendation creates and populates the private SQLite cache automatically; there is no separate prefetch or migration step. A complete normalized catalog snapshot lasts 24 hours; a provider-limited partial catalog and injury/news snapshots are retried after five minutes. Fresh snapshots survive server restarts. If an expired snapshot cannot refresh, last-known-good identity data may be retained, but recommendation risk remains unknown and stale headlines are not shown as recent news.

See [FantasyPros injury/news cache](../README.md#fantasypros-injurynews-cache) for request limits, terms/attribution, cache contents, and privacy behavior.

## Agent handoff

### Automatic MCP sync

After a changed scan, the extension posts sanitized context to the loopback-only `/draft-sync` route. An MCP client should:

1. Call `ff_get_live_draft_state` with the exact `league_id`.
2. Check ledger, state-age, and identity warnings.
3. Call `ff_get_live_draft_recommendation` with the same `league_id` only when the state is ready.

The sidebar/dashboard sends only an allowlisted league ID and bounded strategy/count/ranking/simulation settings to the local recommendation route. The server independently loads the exact saved session and profile, then rechecks both after scoring. If a pick or profile changed mid-request, it discards the candidates and asks for a refresh.

### Manual export

Select **Agent JSON** to download:

- The complete ordered pick ledger
- Your roster
- Rosters grouped by fantasy team
- Current and next overall-pick numbers
- Sanitized draft identifiers

CSV remains available for spreadsheet use.

## Troubleshooting

### Agent sync is offline

- Confirm the server is running at `127.0.0.1:8765`.
- Reload the extension and exact Yahoo tab.
- Open **Results → Round by Round** and select **Rescan page**.
- If an `.env` key just changed, restart the server before rescanning.

### Firefox says `Permission denied to access property "then"`

The current extension avoids Firefox's content-script Promise boundary by serializing same-draft work through its background broker; no additional website permission is needed. Reload the temporary add-on in `about:debugging`, close stale tabs for that mock, reload the exact active tab, show **Results → Round by Round**, and rescan. If the error persists on the current checkout, use **Save diagnostics** before reporting it.

### Yahoo league identity could not be resolved

- Open and rescan the exact active tab rather than choosing a latest saved draft.
- Open the dashboard from that popup so its league ID is selected.
- For an Instant Mock Draft or unavailable Yahoo API, import or explicitly bind a saved local profile, then refresh. This path should make zero Yahoo calls.
- For a normal league using Yahoo fallback, first verify `ff_get_leagues` returns the matching league and authenticated team.

### Popup reports ledger defects

It lists the exact missing/duplicate numbers and sanitized unnumbered details. Show the complete Round-by-Round table and use **Full rescan & repair**. Conflicting or malformed Yahoo table copies produce a recovery error instead of being treated as healthy.

### Yahoo rows were found but not parsed

Yahoo may have changed its layout. Select **Save diagnostics**. The report contains structural counters and allowlisted field-presence counts only; it excludes raw page text, CSS classes, test IDs, ARIA text, page locations, chat, and manager text.

## Correctness and privacy guarantees

- Numbered **Results → Round by Round** data is authoritative; gaps, duplicates, and unnumbered picks block recommendations.
- Per-league browser keys and server validation prevent one league from overwriting another.
- Durable repair/reset journals reconcile interrupted work before stale scanning or sync can resume.
- A packaged background lock broker serializes same-league scans, repairs, and resets across tabs; a lost lease aborts protected work and leaves the journal recoverable.
- Loopback endpoints allowlist fields, validate the exact session, cap payloads, restrict origins, and return recommendation responses with `Cache-Control: no-store`.
- Server-side draft/profile/cache files use user-only permissions.
- The extension stores no Yahoo credentials, cookies, OAuth parameters, full page locations, or arbitrary browser fields.
- Recommendations never draft a player or inject controls into Yahoo.

## Store preparation

`manifest.json` declares a stable Firefox add-on ID, Firefox 142 and Chrome 121 minimum versions, no Firefox data collection, the `storage` permission, narrowly scoped Yahoo draft-page access, and loopback access. [CHROMEWEBSTORE.md](CHROMEWEBSTORE.md) tracks listing copy, permission justifications, disclosures, and the Chrome submission checklist.

## Tests

No package installation is required for extension tests; they use Node's built-in test runner:

```bash
npm --prefix chrome-extension test
```

Tests cover authoritative parsing, duplicate/gap persistence, privacy-minimal diagnostics, guarded repair/reset, cross-league storage, background-broker serialization/recovery, loopback sync and recommendation requests, explicit league selection, profile import/reuse, inert text rendering, Firefox/Chrome compatibility, and export safety.
