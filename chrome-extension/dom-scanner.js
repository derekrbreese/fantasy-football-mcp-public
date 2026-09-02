(function initDomScanner(globalScope) {
  'use strict';

  const CANDIDATE_SELECTOR = [
    '[data-pick-number]',
    '[data-overall-pick]',
    '[data-pick]',
    '[data-testid*="pick" i]',
    '[data-test*="pick" i]',
    '[class*="draft-pick" i]',
    '[class*="draftPick"]',
    '[class*="pick-row" i]',
    '[aria-label*="draft pick" i]',
    '[aria-label^="pick " i]',
    '[class*="draft" i] [role="row"]',
    '[data-testid*="draft" i] [role="row"]',
  ].join(',');

  const FIELD_SELECTORS = {
    player: [
      '[data-player-name]',
      '[data-testid*="player-name" i]',
      '[data-test*="player-name" i]',
      '[class*="player-name" i]',
      '[class*="playerName"]',
    ],
    position: [
      '[data-position]',
      '[data-testid*="position" i]',
      '[class*="position" i]',
    ],
    nflTeam: [
      '[data-nfl-team]',
      '[data-testid*="nfl-team" i]',
      '[class*="nfl-team" i]',
    ],
    fantasyTeam: [
      '[data-fantasy-team]',
      '[data-team-name]',
      '[data-testid*="team-name" i]',
      '[data-test*="team-name" i]',
      '[class*="team-name" i]',
      '[class*="teamName"]',
    ],
    pickNumber: ['[data-pick-number]', '[data-overall-pick]'],
    roundNumber: ['[data-round-number]', '[data-round]'],
    roundPick: ['[data-round-pick]', '[data-pick-in-round]'],
  };

  const ATTRIBUTE_NAMES = [
    'aria-label',
    'data-pick-number',
    'data-overall-pick',
    'data-pick',
    'data-round-number',
    'data-round',
    'data-round-pick',
    'data-pick-in-round',
    'data-player-name',
    'data-player',
    'data-position',
    'data-nfl-team',
    'data-team-name',
    'data-fantasy-team',
    'data-manager-name',
  ];

  function clean(value) {
    return String(value ?? '').replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').trim();
  }

  function normalizePlayerKey(value) {
    if (typeof value !== 'string') return null;
    const playerKey = value.trim();
    return /^[1-9]\d{0,9}\.p\.[1-9]\d{0,9}$/.test(playerKey) ? playerKey : null;
  }

  function extractYahooPlayerKey(element) {
    if (!element) return null;
    const playerKeys = new Set();
    const addPlayerKey = (value) => {
      const playerKey = normalizePlayerKey(value);
      if (playerKey) playerKeys.add(playerKey);
      return playerKeys.size <= 1;
    };

    for (const attribute of ['data-player-key', 'data-yahoo-player-key']) {
      if (!addPlayerKey(element.getAttribute?.(attribute))) return null;
      const nested = element.querySelectorAll
        ? [...element.querySelectorAll(`[${attribute}]`)]
        : [element.querySelector?.(`[${attribute}]`)].filter(Boolean);
      if (nested.length > 20) return null;
      for (const node of nested) {
        if (!addPlayerKey(node.getAttribute?.(attribute))) return null;
      }
    }

    const anchors = [...(element.querySelectorAll?.('a[href]') || [])];
    if (anchors.length > 20) return null;
    for (const anchor of anchors) {
      const href = anchor.getAttribute?.('href');
      if (typeof href !== 'string' || href.length > 500) continue;
      let url;
      try {
        url = new URL(href, 'https://football.fantasysports.yahoo.com/');
      } catch (_error) {
        continue;
      }
      if (url.hostname !== 'football.fantasysports.yahoo.com') continue;
      if (!/(?:^|\/)(?:player|players|playernote|playercard)(?:\/|$)/i.test(url.pathname)) {
        continue;
      }
      for (const parameter of ['player_key', 'playerKey']) {
        for (const queryValue of url.searchParams.getAll(parameter)) {
          if (!addPlayerKey(queryValue)) return null;
        }
      }
      for (const pathPart of url.pathname.split('/')) {
        if (!addPlayerKey(pathPart)) return null;
      }
    }
    return playerKeys.size === 1 ? [...playerKeys][0] : null;
  }

  function readField(element, selectors) {
    const node = element.querySelector(selectors.join(','));
    return clean(node?.textContent || node?.getAttribute?.('aria-label')) || undefined;
  }

  function snapshotPickElement(element) {
    const text = clean(element?.textContent);
    if (!text || text.length > 800) return null;

    const attributes = {};
    for (const name of ATTRIBUTE_NAMES) {
      const value = clean(element.getAttribute?.(name));
      if (value) attributes[name] = value;
    }

    const labels = {};
    for (const [field, selectors] of Object.entries(FIELD_SELECTORS)) {
      const value = readField(element, selectors);
      if (value) labels[field] = value;
    }
    const playerKey = extractYahooPlayerKey(element);
    if (playerKey) labels.playerKey = playerKey;

    return { text, attributes, labels };
  }

  function findPickSnapshots(root) {
    const elements = root?.querySelectorAll?.(CANDIDATE_SELECTOR) || [];
    const controlledSidebarPanels = new Set(
      findPairedQueueAndPicksControls(root)
        .flatMap(({ queuePanel, picksPanel }) => [queuePanel, picksPanel])
        .filter(Boolean),
    );
    const snapshots = [];
    const seen = new Set();
    for (const element of elements) {
      if (seen.has(element)) continue;
      seen.add(element);
      if (!isRenderedElement(element)) continue;
      const containingPanel = element.closest?.('[role="tabpanel"]');
      if (
        controlledSidebarPanels.has(containingPanel) ||
        [...controlledSidebarPanels].some((panel) => panel?.contains?.(element))
      ) continue;
      const snapshot = snapshotPickElement(element);
      if (snapshot) snapshots.push(snapshot);
    }
    return snapshots;
  }

  function exactTabText(tab) {
    return clean(tab?.innerText || tab?.textContent).replace(/\s+/g, ' ');
  }

  function controlledPanel(root, tab) {
    const id = clean(tab?.getAttribute?.('aria-controls'));
    if (!id || id.length > 100 || !/^[A-Za-z][\w:.-]*$/.test(id)) return null;
    const documentRoot = tab?.ownerDocument || root;
    const panel = documentRoot?.getElementById?.(id) || root?.getElementById?.(id);
    if (!panel || clean(panel.getAttribute?.('role')).toLowerCase() !== 'tabpanel') return null;
    const labelledBy = clean(panel.getAttribute?.('aria-labelledby'));
    if (labelledBy && (!tab.id || !labelledBy.split(/\s+/).includes(tab.id))) return null;
    return panel;
  }

  function findPairedQueueAndPicksControls(root) {
    const tabs = [...(root?.querySelectorAll?.('[role="tab"]') || [])];
    const tablists = new Map();
    for (const tab of tabs) {
      const tablist = tab.closest?.('[role="tablist"]');
      if (!tablist) continue;
      if (!tablists.has(tablist)) tablists.set(tablist, []);
      tablists.get(tablist).push(tab);
    }

    const pairs = [];
    for (const [tablist, groupedTabs] of tablists) {
      const picksTabs = groupedTabs.filter((tab) => /^Picks$/i.test(exactTabText(tab)));
      const queueTabs = groupedTabs.filter((tab) => /^Queue$/i.test(exactTabText(tab)));
      if (picksTabs.length !== 1 || queueTabs.length !== 1) continue;
      const picksPanel = controlledPanel(root, picksTabs[0]);
      const queuePanel = controlledPanel(root, queueTabs[0]);
      if (!picksPanel || picksPanel === queuePanel) continue;
      pairs.push({
        tablist,
        picksTab: picksTabs[0],
        queueTab: queueTabs[0],
        picksPanel,
        queuePanel,
      });
    }
    return pairs;
  }

  const PANEL_DETAILS_PATTERN = /^(QB|RB|WR|TE|K|DEF|DST|D\/ST)\s*([•·])\s*[A-Z]{2,3}\s*\2\s*Bye\s+(\d{1,2})$/i;
  const PANEL_STATUS_PATTERN = /^(?:Q|O|D|IR|PUP|NFI|SUSP)$/i;
  const PANEL_PLAYER_PATTERN = /^[\p{L}\p{M}\p{N} .,'’&()/-]+$/u;
  const PANEL_TEAM_PATTERN = /^[\p{L}\p{M}\p{N} .,'’&()_!#-]+$/u;

  function safePanelField(value, maximumLength, pattern) {
    const normalized = clean(value);
    if (
      !normalized ||
      normalized.length > maximumLength ||
      /[\r\n]/.test(normalized) ||
      /(?:https?:\/\/|[?][^\s]*=|[<>])/i.test(normalized) ||
      !/\p{L}/u.test(normalized) ||
      !pattern.test(normalized)
    ) return null;
    return normalized;
  }

  function snapshotPicksPanelElement(element) {
    const source = typeof element?.innerText === 'string'
      ? element.innerText
      : element?.textContent;
    if (!source || String(source).length > 500 || !isRenderedElement(element)) return null;
    const lines = String(source)
      .split(/\r?\n/)
      .map(clean)
      .filter(Boolean);
    if (lines.length < 4 || lines.length > 6) return null;

    const numberIndexes = lines
      .map((line, index) => (/^[1-9]\d{0,2}$/.test(line) && Number(line) <= 500 ? index : -1))
      .filter((index) => index >= 0);
    const detailsIndexes = lines
      .map((line, index) => {
        const match = line.match(PANEL_DETAILS_PATTERN);
        const byeWeek = match ? Number.parseInt(match[3], 10) : 0;
        return match && byeWeek >= 1 && byeWeek <= 18 ? index : -1;
      })
      .filter((index) => index >= 0);
    if (numberIndexes.length !== 1 || detailsIndexes.length !== 1) return null;

    const numberIndex = numberIndexes[0];
    const detailsIndex = detailsIndexes[0];
    if (numberIndex >= detailsIndex) return null;
    const playerLines = lines
      .slice(numberIndex + 1, detailsIndex)
      .filter((line) => !PANEL_STATUS_PATTERN.test(line));
    const teamLines = [
      ...lines.slice(0, numberIndex),
      ...lines.slice(detailsIndex + 1),
    ];
    if (playerLines.length !== 1 || teamLines.length !== 1) return null;

    const playerText = safePanelField(playerLines[0], 80, PANEL_PLAYER_PATTERN);
    const fantasyTeamText = safePanelField(teamLines[0], 80, PANEL_TEAM_PATTERN);
    if (!playerText || !fantasyTeamText || /\bjoined\b/i.test(fantasyTeamText)) return null;
    const snapshot = {
      pickNumberText: lines[numberIndex],
      playerText,
      detailsText: lines[detailsIndex],
      fantasyTeamText,
    };
    const playerKey = extractYahooPlayerKey(element);
    if (playerKey) snapshot.playerKey = playerKey;
    return snapshot;
  }

  function findPicksPanelSnapshots(root) {
    const activePairs = findPairedQueueAndPicksControls(root).filter((pair) => (
      clean(pair.picksTab.getAttribute?.('aria-selected')).toLowerCase() === 'true' &&
      clean(pair.queueTab.getAttribute?.('aria-selected')).toLowerCase() !== 'true' &&
      isRenderedElement(pair.tablist) &&
      isRenderedElement(pair.picksTab) &&
      isRenderedElement(pair.picksPanel)
    ));
    if (activePairs.length !== 1) return [];

    const descendants = [...(activePairs[0].picksPanel.querySelectorAll?.('*') || [])]
      .slice(0, 1500);
    const snapshotsBySignature = new Map();
    for (const element of descendants) {
      const snapshot = snapshotPicksPanelElement(element);
      if (!snapshot) continue;
      const signature = JSON.stringify(snapshot);
      if (!snapshotsBySignature.has(signature)) snapshotsBySignature.set(signature, snapshot);
    }
    return [...snapshotsBySignature.values()].sort(
      (left, right) => Number(left.pickNumberText) - Number(right.pickNumberText),
    );
  }

  function findRoundByRoundSnapshots(root) {
    const tables = root?.querySelectorAll?.('table') || [];
    const snapshots = [];

    for (const table of tables) {
      if (!isRoundByRoundTable(table)) continue;
      snapshots.push(...snapshotRoundByRoundTable(table, false));
    }

    return snapshots;
  }

  function isRoundByRoundTable(table) {
    const heading = clean(table.querySelector?.('thead')?.innerText || table.querySelector?.('thead')?.textContent);
    return /^Pick\s+Player\s+Team$/i.test(heading.replace(/\s+/g, ' '));
  }

  function snapshotRoundByRoundTable(table, includeMalformed) {
    const snapshots = [];
    let roundText;
    const rows = table.querySelectorAll?.('tr') || [];
    for (const row of rows) {
      const rowText = clean(row.innerText || row.textContent);
      const roundMatch = rowText.match(/^ROUND\s+\d+$/i);
      if (roundMatch) {
        roundText = roundMatch[0];
        continue;
      }

      if (!rowText || /^Pick\s+Player\s+Team$/i.test(rowText.replace(/\s+/g, ' '))) continue;

      const cells = row.querySelectorAll?.('td') || [];
      const roleCells = includeMalformed
        ? (row.querySelectorAll?.('[role="cell"]') || [])
        : [];
      if (roleCells.length) {
        snapshots.push({ roundText, cellShape: `role-cell:${roleCells.length}` });
        continue;
      }
      if (cells.length !== 3) {
        if (!includeMalformed) continue;
        snapshots.push({
          roundText,
          cellShape: `td:${cells.length}`,
        });
        continue;
      }
      const pickText = clean(cells[0].innerText || cells[0].textContent);
      if (!includeMalformed && !/^\d+$/.test(pickText)) continue;
      const snapshot = {
        roundText,
        pickText,
        playerText: clean(cells[1].innerText || cells[1].textContent),
        fantasyTeamText: clean(cells[2].innerText || cells[2].textContent),
      };
      const playerKey = extractYahooPlayerKey(cells[1]);
      if (playerKey) snapshot.playerKey = playerKey;
      snapshots.push(snapshot);
    }
    return snapshots;
  }

  function scanAuthoritativeRoundByRoundTables(root) {
    const matchingTables = [...(root?.querySelectorAll?.('table') || [])]
      .filter(isRoundByRoundTable);
    if (matchingTables.length === 0) {
      return {
        ok: false,
        tableCount: 0,
        distinctTableCount: 0,
        apparentRowCount: 0,
        snapshots: [],
        error: 'No Round-by-Round ledger is visible. Open Results → Round by Round and try again.',
      };
    }

    const tablesBySignature = new Map();
    for (const table of matchingTables) {
      const snapshots = snapshotRoundByRoundTable(table, true);
      const signature = JSON.stringify(snapshots);
      if (!tablesBySignature.has(signature)) tablesBySignature.set(signature, snapshots);
    }
    if (tablesBySignature.size !== 1) {
      return {
        ok: false,
        tableCount: matchingTables.length,
        distinctTableCount: tablesBySignature.size,
        apparentRowCount: 0,
        snapshots: [],
        error: 'Yahoo shows conflicting Round-by-Round tables. Reload Results → Round by Round before repairing; saved picks were not changed.',
      };
    }

    const snapshots = tablesBySignature.values().next().value;
    return {
      ok: true,
      tableCount: matchingTables.length,
      distinctTableCount: 1,
      apparentRowCount: snapshots.length,
      snapshots,
    };
  }

  function isRenderedElement(element) {
    if (!element || element.hidden === true) return false;
    if (clean(element.getAttribute?.('aria-hidden')).toLowerCase() === 'true') return false;
    if (element.closest?.('[hidden], [aria-hidden="true"]')) return false;
    if (typeof element.checkVisibility === 'function' && !element.checkVisibility()) return false;
    if (typeof element.getClientRects === 'function' && element.getClientRects().length === 0) {
      return false;
    }
    const view = element.ownerDocument?.defaultView;
    if (typeof view?.getComputedStyle === 'function') {
      const style = view.getComputedStyle(element);
      if (
        style?.display === 'none'
        || ['hidden', 'collapse'].includes(style?.visibility)
        || style?.contentVisibility === 'hidden'
      ) return false;
    }
    return true;
  }

  function renderedElementText(element) {
    const source = typeof element?.innerText === 'string'
      ? element.innerText
      : element?.textContent;
    const text = clean(source);
    return text && isRenderedElement(element) ? text : '';
  }

  function findCurrentPickNumber(root) {
    const elements = root?.querySelectorAll?.('body *') || [];
    const pickNumbers = new Set();
    for (const element of elements) {
      const text = renderedElementText(element);
      if (!text || text.length > 300) continue;
      const match = text.replace(/\s+/g, ' ').match(/\bROUND\s+\d+\s*[,•·-]?\s*PICK\s*#?\s*(\d+)\b/i);
      const pickNumber = match ? Number.parseInt(match[1], 10) : 0;
      if (pickNumber > 0) pickNumbers.add(pickNumber);
      if (pickNumbers.size > 1) return null;
    }
    return pickNumbers.values().next().value || null;
  }

  function findLiveDraftSnapshot(root) {
    const elements = root?.querySelectorAll?.('body *') || [];
    let statusText;
    let lastPickText;
    let lastPickElement;

    for (const element of elements) {
      const text = clean(element?.innerText || element?.textContent);
      if (!text || text.length > 300) continue;
      const flatText = text.replace(/\s+/g, ' ');
      if (/\bROUND\s+\d+\s*[,•·-]?\s*PICK\s*#?\s*\d+\b/i.test(flatText) || /^Draft Paused$/i.test(flatText)) {
        if (!statusText || text.length < statusText.length) statusText = text;
      }
      if (/^Last\s*:.*\)\s*\S/i.test(flatText)) {
        if (!lastPickText || text.length < lastPickText.length) {
          lastPickText = text;
          lastPickElement = element;
        }
      }
    }

    if (!statusText || !lastPickText) return null;
    const snapshot = { statusText, lastPickText };
    const playerKey = extractYahooPlayerKey(lastPickElement);
    if (playerKey) snapshot.playerKey = playerKey;
    return snapshot;
  }

  function collectDiagnosticSnapshots(root) {
    const candidates = [...(root?.querySelectorAll?.(CANDIDATE_SELECTOR) || [])];
    const fieldPresence = {
      pickNumber: 0,
      roundNumber: 0,
      roundPick: 0,
      player: 0,
      position: 0,
      nflTeam: 0,
      fantasyTeam: 0,
      playerKey: 0,
    };
    let snapshottedCandidateCount = 0;
    for (const candidate of candidates) {
      const snapshot = snapshotPickElement(candidate);
      if (!snapshot) continue;
      snapshottedCandidateCount += 1;
      const attributes = snapshot.attributes || {};
      const labels = snapshot.labels || {};
      if (labels.pickNumber || attributes['data-pick-number'] || attributes['data-overall-pick'] || attributes['data-pick']) fieldPresence.pickNumber += 1;
      if (labels.roundNumber || attributes['data-round-number'] || attributes['data-round']) fieldPresence.roundNumber += 1;
      if (labels.roundPick || attributes['data-round-pick'] || attributes['data-pick-in-round']) fieldPresence.roundPick += 1;
      for (const field of ['player', 'position', 'nflTeam', 'fantasyTeam']) {
        if (labels[field]) fieldPresence[field] += 1;
      }
      if (labels.playerKey) fieldPresence.playerKey += 1;
    }
    const ledgerScan = scanAuthoritativeRoundByRoundTables(root);
    const picksPanelSnapshots = findPicksPanelSnapshots(root);
    return {
      candidateCount: candidates.length,
      snapshottedCandidateCount,
      roundByRoundTableCount: ledgerScan.tableCount,
      roundByRoundDistinctTableCount: ledgerScan.distinctTableCount,
      roundByRoundApparentRowCount: ledgerScan.apparentRowCount,
      picksPanelSnapshotCount: picksPanelSnapshots.length,
      fieldPresence,
    };
  }

  const api = {
    CANDIDATE_SELECTOR,
    collectDiagnosticSnapshots,
    extractYahooPlayerKey,
    findCurrentPickNumber,
    findLiveDraftSnapshot,
    findPicksPanelSnapshots,
    findPickSnapshots,
    findRoundByRoundSnapshots,
    scanAuthoritativeRoundByRoundTables,
    snapshotPickElement,
  };
  globalScope.YahooDraftDomScanner = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
