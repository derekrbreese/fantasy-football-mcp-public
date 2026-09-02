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

  function evaluateAuthoritativeLedgerScan(scan, parsedResults, currentPickNumber = null) {
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
    const trustworthyCurrentPick = positiveInteger(currentPickNumber);
    const snapshots = Array.isArray(scan.snapshots) ? scan.snapshots : [];
    const authoritativePicks = [];
    const unparsedCompletedPickNumbers = [];
    let unparsedStructuralRowCount = 0;
    let ignoredFutureRowCount = 0;

    for (let index = 0; index < scan.apparentRowCount; index += 1) {
      const parsedPick = parsedResults[index] || null;
      const snapshot = snapshots[index] || (
        parsedPick?.pickNumber ? { pickText: String(parsedPick.pickNumber) } : {}
      );
      const pickText = String(snapshot.pickText ?? '').trim();
      const hasNormalNumberedShape = !snapshot.cellShape && /^\d+$/.test(pickText);
      const snapshotPickNumber = hasNormalNumberedShape ? positiveInteger(pickText) : null;

      if (!hasNormalNumberedShape || !snapshotPickNumber) {
        unparsedStructuralRowCount += 1;
        continue;
      }
      if (parsedPick) {
        authoritativePicks.push(parsedPick);
        continue;
      }
      if (trustworthyCurrentPick && snapshotPickNumber >= trustworthyCurrentPick) {
        ignoredFutureRowCount += 1;
        continue;
      }
      unparsedCompletedPickNumbers.push(snapshotPickNumber);
    }

    const unparsedRowCount = unparsedCompletedPickNumbers.length + unparsedStructuralRowCount;
    if (unparsedRowCount > 0) {
      const details = [];
      if (unparsedCompletedPickNumbers.length) {
        details.push(`completed picks ${unparsedCompletedPickNumbers.join(', ')} did not parse safely`);
      }
      if (unparsedStructuralRowCount) {
        const noun = unparsedStructuralRowCount === 1 ? 'row' : 'rows';
        details.push(`${unparsedStructuralRowCount} ${noun} had no safe positive pick number or normal three-cell shape`);
      }
      return {
        authoritativePicks: null,
        health: null,
        unparsedCompletedPickNumbers,
        unparsedStructuralRowCount,
        ignoredFutureRowCount,
        error: `Yahoo showed ${scan.apparentRowCount} Round-by-Round candidate rows, but ${details.join('; ')}. Reload Results → Round by Round before repairing.`,
      };
    }
    const completedPicks = ignoredFutureRowCount === 0
      && authoritativePicks.length === parsedResults.length
      ? parsedResults
      : authoritativePicks;
    return {
      authoritativePicks: completedPicks,
      health: summarizeNumberedLedgerHealth(analyzeLedger(completedPicks)),
      unparsedCompletedPickNumbers,
      unparsedStructuralRowCount,
      ignoredFutureRowCount,
      error: null,
    };
  }

  function validateStableCurrentPick(beforeScan, afterScan) {
    const before = positiveInteger(beforeScan);
    const after = positiveInteger(afterScan);
    if (before === after) return { ok: true, currentPickNumber: before };
    return {
      ok: false,
      currentPickNumber: null,
      error: `Yahoo’s current pick changed from ${before || 'unavailable'} to ${after || 'unavailable'} while the Round-by-Round ledger was scanned. Saved picks were not changed.`,
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
    validateStableCurrentPick,
  };
  globalScope.YahooDraftLedgerHealth = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
