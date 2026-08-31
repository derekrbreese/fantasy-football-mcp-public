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
      const heading = clean(table.querySelector?.('thead')?.innerText || table.querySelector?.('thead')?.textContent);
      if (!/^Pick\s+Player\s+Team$/i.test(heading.replace(/\s+/g, ' '))) continue;

      let roundText;
      const rows = table.querySelectorAll?.('tr') || [];
      for (const row of rows) {
        const rowText = clean(row.innerText || row.textContent);
        const roundMatch = rowText.match(/^ROUND\s+\d+$/i);
        if (roundMatch) {
          roundText = roundMatch[0];
          continue;
        }

        const cells = row.querySelectorAll?.('td') || [];
        if (cells.length !== 3 || !/^\s*\d+\s*$/.test(cells[0].innerText || cells[0].textContent || '')) continue;
        snapshots.push({
          roundText,
          pickText: clean(cells[0].innerText || cells[0].textContent),
          playerText: clean(cells[1].innerText || cells[1].textContent),
          fantasyTeamText: clean(cells[2].innerText || cells[2].textContent),
        });
      }
    }

    return snapshots;
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
    const elements = root?.querySelectorAll?.('body *') || [];
    const diagnostics = [];
    const seen = new Set();
    const footballSignal = /\b(?:QB|RB|WR|TE|K|DEF|DST|WRT|BN|bye|round|pick|draft)\b/i;

    for (const element of elements) {
      const text = clean(element?.innerText || element?.textContent);
      if (!text || text.length > 600 || !footballSignal.test(text)) continue;

      const snapshot = {
        tag: clean(element.tagName).toLowerCase(),
        role: clean(element.getAttribute?.('role')) || undefined,
        className: clean(element.className).slice(0, 200) || undefined,
        testId: clean(element.getAttribute?.('data-testid') || element.getAttribute?.('data-test')) || undefined,
        ariaLabel: clean(element.getAttribute?.('aria-label')).slice(0, 200) || undefined,
        childCount: Number(element.childElementCount || 0),
        text,
      };
      for (const key of Object.keys(snapshot)) {
        if (snapshot[key] === undefined) delete snapshot[key];
      }

      const signature = JSON.stringify(snapshot);
      if (seen.has(signature)) continue;
      seen.add(signature);
      diagnostics.push(snapshot);
      if (diagnostics.length >= 500) break;
    }
    return diagnostics;
  }

  const api = {
    CANDIDATE_SELECTOR,
    collectDiagnosticSnapshots,
    findLiveDraftSnapshot,
    findPickSnapshots,
    findRoundByRoundSnapshots,
    snapshotPickElement,
  };
  globalScope.YahooDraftDomScanner = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
