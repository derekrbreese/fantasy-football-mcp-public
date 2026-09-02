# Chrome Web Store submission notes

Keep this file aligned with `manifest.json` and the extension's actual behavior.

## Single purpose

Yahoo Fantasy Draft Recorder records completed picks shown in a Yahoo Fantasy Football live draft and lets the user review, export, or use those picks for recommendations from a fantasy-football MCP server running on the same computer.

## Suggested listing

**Name:** Yahoo Fantasy Draft Recorder

**Summary:** Record Yahoo Fantasy Football live draft picks and share them with your local recommendation agent.

**Detailed description:**

Yahoo Fantasy Draft Recorder watches the Round-by-Round results on an open Yahoo Fantasy Football draft page. It records the overall pick, round, player, NFL position and team, drafting fantasy team, and recording time. Open the extension popup to review the latest picks, repair or rescan the ledger, clear local history, export CSV, download agent-ready JSON, or open the local Draft Assistant dashboard.

When the user's fantasy-football MCP server is running locally, the extension sends sanitized draft context to `127.0.0.1` so the user's own agent can make next-pick recommendations. A recommendation request contains only a schema version, explicit league ID, and bounded strategy/count/simulation settings. Chrome users can open the full loopback dashboard; Firefox additionally exposes the same read-only recommendations in a persistent sidebar. Neither surface drafts players or modifies Yahoo. The extension does not send draft data to the extension developer or a third party, read Yahoo credentials, or retain Yahoo authentication URL parameters.

## Permission justifications

### `storage`

Required to save recorded draft picks and bounded league-specific queue preferences in `chrome.storage.local` so they remain available when the popup/sidebar closes or the Yahoo draft page reloads. It does not store cookies, credentials, or Yahoo URL authentication parameters.

### `notifications`

Required for the Firefox Draft Assistant's explicit opt-in turn alerts. An alert is considered only after a current recommendation confirms a complete authoritative ledger and says the user is next or on the clock. Alerts are deduplicated per draft revision, contain only turn status and the current advisory player name, and never select or draft a player. Notifications remain disabled until the user enables them in the sidebar.

### Host access: `https://football.fantasysports.yahoo.com/draftclient/*`

Required to run the content script only on Yahoo Fantasy Sports draft-client pages. The content script reads completed-pick rows from the page DOM and watches that DOM for newly posted picks. Access is restricted to `/draftclient/`; the extension does not request access to unrelated Yahoo pages.

### Host access: `http://127.0.0.1/*`

Required to send sanitized draft context to the user's optional fantasy-football MCP server on the same computer and open its local recommendation UI. The extension uses `http://127.0.0.1:8765/draft-sync`, the explicitly confirmed exact-session `http://127.0.0.1:8765/draft-reset`, `http://127.0.0.1:8765/draft-recommendation`, and `http://127.0.0.1:8765/draft-dashboard` plus packaged dashboard assets served from that loopback application. The dashboard also uses loopback-only profile import routes, `/draft-profiles`, `/draft-profile-bind`, and `/draft-profile-default` to reuse sanitized rankings and roster settings. Only safe profile summaries, an explicit per-sport default pointer, and chosen source/target league IDs cross that UI boundary; recorded picks are never copied by profile reuse. Failed ordinary sync continues to allow browser-only recording and manual export; reset fails closed without deleting browser state when the local server cannot confirm it.

## Data-use disclosure

- **Data handled:** Fantasy sports draft selections, fantasy team names displayed in the draft, local recording timestamps, and bounded per-league player queue/notification preferences chosen by the user.
- **Storage:** Local browser extension storage for draft state and bounded per-league queue/alert preferences and, if enabled by running the local MCP server, a user-owned state file on the same computer.
- **Transmission:** Sanitized draft context and allowlisted recommendation settings are sent only to `127.0.0.1`, the loopback interface. There are no developer-operated endpoints, analytics, advertising, or third-party requests.
- **Sale or sharing:** None.
- **Authentication data:** Not collected or stored.
- **User controls:** Users can explicitly reset only the exact active draft from the popup while preserving its separately imported profile. In the local dashboard they can bind one saved ranking/settings profile to an exact draft, or explicitly set and clear one per-sport default for future profileless drafts. Exact profiles always win and pick ledgers remain isolated. Users can remove all extension data by uninstalling the extension or clearing its site/extension storage.

## Remote code

None. All JavaScript used by the extension is packaged with it. There are no remotely hosted scripts, WebAssembly modules, `eval()` calls, or dynamic code downloads.

## Content and security notes

- Popup, sidebar, and dashboard response content is written with `textContent`, not interpreted as HTML.
- CSV export neutralizes formula-leading characters to reduce spreadsheet injection risk.
- The draft URL parser keeps only the sport, league ID, and team ID path segments; query parameters are discarded.
- The extension requests only `storage`, `notifications`, narrowly scoped Yahoo draft-client host access, and loopback host access for local sync and recommendation UI routes. Notifications are opt-in and advisory.
- The packaged background script is a private lock broker. It receives only the allowlisted Yahoo session key needed to serialize same-session extension work, and keeps only an opaque nonce plus expiry in browser-session storage to fence a background restart. It receives no page URL, query parameters, cookies, credentials, player data, or arbitrary DOM fields.

## Submission checklist

- [ ] Replace or confirm the suggested listing text.
- [x] Package the football extension/store icon through 128×128.
- [ ] Add promotional images in the required sizes.
- [ ] Capture screenshots without private league or manager information.
- [ ] Publish a privacy policy URL if required by the Developer Dashboard.
- [ ] Run `npm --prefix chrome-extension test`.
- [ ] Test loading, recording, rescanning, clearing, both export formats, and the loopback dashboard link in current stable Chrome.
- [ ] Confirm permission justifications still match `manifest.json`.
- [ ] Confirm the extension contains no secrets, private draft URLs, or authentication tokens.
