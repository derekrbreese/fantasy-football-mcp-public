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

    return { text, attributes, labels };
  }

  function findPickSnapshots(root) {
    const elements = root?.querySelectorAll?.(CANDIDATE_SELECTOR) || [];
    const snapshots = [];
    const seen = new Set();
    for (const element of elements) {
      if (seen.has(element)) continue;
      seen.add(element);
      const snapshot = snapshotPickElement(element);
      if (snapshot) snapshots.push(snapshot);
    }
    return snapshots;
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
      snapshots.push({
        roundText,
        pickText,
        playerText: clean(cells[1].innerText || cells[1].textContent),
        fantasyTeamText: clean(cells[2].innerText || cells[2].textContent),
      });
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

    for (const element of elements) {
      const text = clean(element?.innerText || element?.textContent);
      if (!text || text.length > 300) continue;
      const flatText = text.replace(/\s+/g, ' ');
      if (/\bROUND\s+\d+\s*[,•·-]?\s*PICK\s*#?\s*\d+\b/i.test(flatText) || /^Draft Paused$/i.test(flatText)) {
        if (!statusText || text.length < statusText.length) statusText = text;
      }
      if (/^Last\s*:.*\)\s*\S/i.test(flatText)) {
        if (!lastPickText || text.length < lastPickText.length) lastPickText = text;
      }
    }

    return statusText && lastPickText ? { statusText, lastPickText } : null;
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
    }
    const ledgerScan = scanAuthoritativeRoundByRoundTables(root);
    return {
      candidateCount: candidates.length,
      snapshottedCandidateCount,
      roundByRoundTableCount: ledgerScan.tableCount,
      roundByRoundDistinctTableCount: ledgerScan.distinctTableCount,
      roundByRoundApparentRowCount: ledgerScan.apparentRowCount,
      fieldPresence,
    };
  }

  const api = {
    CANDIDATE_SELECTOR,
    collectDiagnosticSnapshots,
    findCurrentPickNumber,
    findLiveDraftSnapshot,
    findPickSnapshots,
    findRoundByRoundSnapshots,
    scanAuthoritativeRoundByRoundTables,
    snapshotPickElement,
  };
  globalScope.YahooDraftDomScanner = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
