# Chrome Web Store submission notes

Keep this file aligned with `manifest.json` and the extension's actual behavior.

## Single purpose

Yahoo Fantasy Draft Recorder records completed picks shown in a Yahoo Fantasy Football live draft and lets the user review, export, or share those picks with a fantasy-football MCP server running on the same computer.

## Suggested listing

**Name:** Yahoo Fantasy Draft Recorder

**Summary:** Record Yahoo Fantasy Football live draft picks and share them with your local recommendation agent.

**Detailed description:**

Yahoo Fantasy Draft Recorder watches the Round-by-Round results on an open Yahoo Fantasy Football draft page. It records the overall pick, round, player, NFL position and team, drafting fantasy team, and recording time. Open the extension popup to review the latest picks, rescan the page, clear local history, export CSV, or download agent-ready JSON.

When the user's fantasy-football MCP server is running locally, the extension sends sanitized draft context to `127.0.0.1` so the user's own agent can make next-pick recommendations. It does not send draft data to the extension developer or a third party, read Yahoo credentials, or retain Yahoo authentication URL parameters.

## Permission justifications

### `storage`

Required to save recorded draft picks in `chrome.storage.local` so history remains available when the popup closes or the Yahoo draft page reloads. The extension stores draft metadata and picks only. It does not store cookies, credentials, or Yahoo URL authentication parameters.

### Host access: `https://football.fantasysports.yahoo.com/draftclient/*`

Required to run the content script only on Yahoo Fantasy Sports draft-client pages. The content script reads completed-pick rows from the page DOM and watches that DOM for newly posted picks. Access is restricted to `/draftclient/`; the extension does not request access to unrelated Yahoo pages.

### Host access: `http://127.0.0.1/*`

Required to send sanitized draft context to the user's optional fantasy-football MCP server on the same computer. The extension uses only `http://127.0.0.1:8765/draft-sync`. Failed connections are ignored so browser-only recording and manual export continue to work without the server.

## Data-use disclosure

- **Data handled:** Fantasy sports draft selections, fantasy team names displayed in the draft, and local recording timestamps.
- **Storage:** Local Chrome extension storage in the user's browser profile and, if enabled by running the local MCP server, a user-owned state file on the same computer.
- **Transmission:** Sanitized draft context is sent only to `127.0.0.1`, the loopback interface. There are no developer-operated endpoints, analytics, advertising, or third-party requests.
- **Sale or sharing:** None.
- **Authentication data:** Not collected or stored.
- **User controls:** Users can clear a draft from the popup and can remove all extension data by uninstalling the extension or clearing its site/extension storage.

## Remote code

None. All JavaScript used by the extension is packaged with it. There are no remotely hosted scripts, WebAssembly modules, `eval()` calls, or dynamic code downloads.

## Content and security notes

- Popup table content is written with `textContent`, not interpreted as HTML.
- CSV export neutralizes formula-leading characters to reduce spreadsheet injection risk.
- The draft URL parser keeps only the sport, league ID, and team ID path segments; query parameters are discarded.
- The extension requests only `storage`, narrowly scoped Yahoo draft-client host access, and loopback host access for the local MCP sync endpoint.

## Submission checklist

- [ ] Replace or confirm the suggested listing text.
- [ ] Add store icon and promotional images in the required sizes.
- [ ] Capture screenshots without private league or manager information.
- [ ] Publish a privacy policy URL if required by the Developer Dashboard.
- [ ] Run `npm --prefix chrome-extension test`.
- [ ] Test loading, recording, rescanning, clearing, and both export formats in current stable Chrome.
- [ ] Confirm permission justifications still match `manifest.json`.
- [ ] Confirm the extension contains no secrets, private draft URLs, or authentication tokens.
