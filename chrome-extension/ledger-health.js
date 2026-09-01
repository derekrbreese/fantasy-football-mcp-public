(function initLedgerHealth(globalScope) {
  'use strict';

  function positiveInteger(value) {
    const number = Number(value);
    return Number.isInteger(number) && number > 0 ? number : null;
  }

  function safeText(value, maxLength = 100) {
    return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, maxLength);
  }

  function safeUnnumberedPick(pick) {
    return {
      player: safeText(pick?.player) || 'Unknown player',
      ...(safeText(pick?.position, 10) ? { position: safeText(pick.position, 10) } : {}),
      ...(safeText(pick?.nflTeam, 10) ? { nflTeam: safeText(pick.nflTeam, 10) } : {}),
      ...(safeText(pick?.fantasyTeam) ? { fantasyTeam: safeText(pick.fantasyTeam) } : {}),
    };
  }

  function analyzeLedger(picks) {
    const counts = new Map();
    const unnumberedPicks = [];
    for (const pick of picks || []) {
      const pickNumber = positiveInteger(pick?.pickNumber);
      if (!pickNumber) {
        unnumberedPicks.push(safeUnnumberedPick(pick));
        continue;
      }
      counts.set(pickNumber, (counts.get(pickNumber) || 0) + 1);
    }

    const numbered = [...counts.keys()].sort((left, right) => left - right);
    const highestPickNumber = numbered.at(-1) || 0;
    const missingPickNumbers = [];
    for (let pickNumber = 1; pickNumber <= highestPickNumber; pickNumber += 1) {
      if (!counts.has(pickNumber)) missingPickNumbers.push(pickNumber);
    }
    const duplicatePickNumbers = numbered.filter((pickNumber) => counts.get(pickNumber) > 1);

    return {
      highestPickNumber,
      missingPickNumbers,
      duplicatePickNumbers,
      unnumberedPicks,
      isComplete: highestPickNumber > 0
        && missingPickNumbers.length === 0
        && duplicatePickNumbers.length === 0
        && unnumberedPicks.length === 0,
    };
  }

  function formatUnnumberedPick(pick) {
    const nfl = [pick.nflTeam, pick.position].filter(Boolean).join(' · ');
    return `${pick.player}${nfl ? ` (${nfl})` : ''}${pick.fantasyTeam ? ` — ${pick.fantasyTeam}` : ''}`;
  }

  function formatLedgerIssues(health) {
    const issues = [];
    if (health.missingPickNumbers.length) {
      issues.push(`Missing picks: ${health.missingPickNumbers.join(', ')}.`);
    }
    if (health.duplicatePickNumbers.length) {
      issues.push(`Duplicate picks: ${health.duplicatePickNumbers.join(', ')}.`);
    }
    if (health.unnumberedPicks.length) {
      issues.push(`Unnumbered picks (${health.unnumberedPicks.length}): ${health.unnumberedPicks.map(formatUnnumberedPick).join('; ')}.`);
    }
    return issues.join(' ');
  }

  function formatRepairFailure(error, health) {
    const issues = health ? formatLedgerIssues(health) : '';
    return [error, issues].filter(Boolean).join(' ');
  }

  function summarizeNumberedLedgerHealth(health) {
    return {
      highestPickNumber: positiveInteger(health?.highestPickNumber) || 0,
      missingPickNumbers: (health?.missingPickNumbers || []).filter(positiveInteger),
      duplicatePickNumbers: (health?.duplicatePickNumbers || []).filter(positiveInteger),
    };
  }

  function mergeVisibleLedgerHealth(authoritativeHealth, savedHealth) {
    const numberedHealth = authoritativeHealth || savedHealth;
    const summary = summarizeNumberedLedgerHealth(numberedHealth);
    const unnumberedPicks = [...(savedHealth?.unnumberedPicks || [])];
    return {
      ...summary,
      unnumberedPicks,
      isComplete: summary.highestPickNumber > 0
        && summary.missingPickNumbers.length === 0
        && summary.duplicatePickNumbers.length === 0
        && unnumberedPicks.length === 0,
    };
  }

  function evaluateAuthoritativeLedgerScan(scan, parsedPicks) {
    if (!scan || scan.tableCount === 0) {
      return { authoritativePicks: null, health: null, error: null };
    }
    if (!scan.ok) {
      return { authoritativePicks: null, health: null, error: scan.error };
    }
    if (scan.apparentRowCount === 0) {
      return {
        authoritativePicks: null,
        health: null,
        error: 'Yahoo’s Round-by-Round table has no completed rows. Wait for it to load before repairing.',
      };
    }
    if (parsedPicks.length !== scan.apparentRowCount) {
      return {
        authoritativePicks: null,
        health: null,
        error: `Yahoo showed ${scan.apparentRowCount} apparent completed ledger rows, but only ${parsedPicks.length} parsed safely. Reload Results → Round by Round before repairing.`,
      };
    }
    return {
      authoritativePicks: parsedPicks,
      health: summarizeNumberedLedgerHealth(analyzeLedger(parsedPicks)),
      error: null,
    };
  }

  function validateLedgerAgainstCurrentPick(health, currentPickNumber) {
    if (!currentPickNumber || health.highestPickNumber === currentPickNumber - 1) {
      return { ok: true };
    }
    return {
      ok: false,
      error: `Visible ledger ends at pick ${health.highestPickNumber}, but Yahoo is currently on pick ${currentPickNumber}. Saved picks were not changed.`,
    };
  }

  function validateDownwardRepairEvidence(savedHealth, proposedHealth, currentPickNumber) {
    if (
      proposedHealth.highestPickNumber >= savedHealth.highestPickNumber
      || currentPickNumber
    ) {
      return { ok: true };
    }
    return {
      ok: false,
      error: `Repair would lower the saved ledger from pick ${savedHealth.highestPickNumber} to pick ${proposedHealth.highestPickNumber}, but Yahoo’s live current pick is unavailable. Saved picks were not changed.`,
    };
  }

  const api = {
    analyzeLedger,
    evaluateAuthoritativeLedgerScan,
    formatLedgerIssues,
    formatRepairFailure,
    mergeVisibleLedgerHealth,
    summarizeNumberedLedgerHealth,
    validateDownwardRepairEvidence,
    validateLedgerAgainstCurrentPick,
  };
  globalScope.YahooDraftLedgerHealth = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
