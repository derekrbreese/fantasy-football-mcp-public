(function initDraftExport(globalScope) {
  'use strict';

  const columns = [
    ['Overall Pick', 'pickNumber'],
    ['Round', 'roundNumber'],
    ['Round Pick', 'roundPick'],
    ['Player', 'player'],
    ['Position', 'position'],
    ['NFL Team', 'nflTeam'],
    ['Fantasy Team', 'fantasyTeam'],
    ['Your Pick', 'isUserPick'],
    ['Recorded At', 'recordedAt'],
  ];

  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    if (/[",\r\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
    return text;
  }

  function picksToCsv(picks) {
    const rows = [columns.map(([heading]) => heading).join(',')];
    for (const pick of picks || []) {
      rows.push(columns.map(([, key]) => csvCell(pick[key])).join(','));
    }
    return rows.join('\r\n');
  }

  function picksToJson(picks) {
    return `${JSON.stringify(picks || [], null, 2)}\n`;
  }

  const api = { picksToCsv, picksToJson };
  globalScope.YahooDraftExport = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
