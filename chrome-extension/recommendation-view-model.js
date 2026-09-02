(function initRecommendationViewModel(globalScope) {
  'use strict';

  const INJURY_STATUSES = new Set([
    'healthy',
    'probable',
    'questionable',
    'doubtful',
    'out',
    'ir',
    'pup',
    'nfi',
    'not active',
    'suspended',
    'day-to-day',
  ]);

  function safeText(value, maximum = 300, fallback = '') {
    if (typeof value !== 'string' && typeof value !== 'number') return fallback;
    const text = String(value).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '').trim();
    return text.slice(0, maximum) || fallback;
  }

  function finiteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  function positiveIntegers(values) {
    if (!Array.isArray(values)) return [];
    return [...new Set(values
      .filter((value) => typeof value === 'number' && Number.isInteger(value) && value > 0))]
      .sort((left, right) => left - right)
      .slice(0, 40);
  }

  function percentage(value) {
    const number = finiteNumber(value);
    if (number === null) return null;
    return `${Math.round(Math.max(0, Math.min(1, number)) * 100)}%`;
  }

  function isoTimestamp(value) {
    const text = safeText(value, 40);
    return text && Number.isFinite(Date.parse(text)) ? text : '';
  }

  function uniqueStrings(values, maximum = 12, textMaximum = 300) {
    const result = [];
    for (const value of values) {
      const text = safeText(value, textMaximum);
      if (text && !result.includes(text)) result.push(text);
      if (result.length >= maximum) break;
    }
    return result;
  }

  function machineCode(value) {
    return safeText(value, 40, 'unavailable')
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '_');
  }

  function advisoryLatency(value) {
    const number = finiteNumber(value);
    if (number === null) return null;
    return Math.round(Math.max(0, Math.min(60_000, number)));
  }

  function advisoryCritic(rawCritic) {
    if (
      !rawCritic ||
      typeof rawCritic !== 'object' ||
      Array.isArray(rawCritic) ||
      rawCritic.advisoryOnly !== true ||
      !['available', 'unavailable'].includes(rawCritic.status)
    ) return null;

    const critic = {
      status: rawCritic.status,
      provider: safeText(rawCritic.provider, 40, 'Databricks'),
      model: safeText(rawCritic.model, 120, 'Model unavailable'),
      advisoryOnly: true,
      cached: rawCritic.cached === true,
      latencyMs: advisoryLatency(rawCritic.latencyMs),
    };
    if (rawCritic.status === 'available') {
      return {
        ...critic,
        summary: safeText(
          rawCritic.summary,
          600,
          'The optional AI critic did not provide a summary.',
        ),
        cautions: uniqueStrings(
          Array.isArray(rawCritic.cautions) ? rawCritic.cautions : [],
          6,
          240,
        ),
        unavailableReason: null,
      };
    }

    const rawReason = rawCritic.unavailableReason;
    const reason = rawReason && typeof rawReason === 'object' && !Array.isArray(rawReason)
      ? rawReason
      : {};
    return {
      ...critic,
      summary: '',
      cautions: [],
      unavailableReason: {
        code: machineCode(reason.code),
        message: safeText(
          reason.message,
          300,
          'The optional AI critic is unavailable. Deterministic recommendations remain available.',
        ),
      },
    };
  }

  function rosterSummary(roster) {
    const players = Array.isArray(roster) ? roster.slice(0, 40) : [];
    const counts = new Map();
    for (const player of players) {
      const position = safeText(player?.position, 16, 'Unknown');
      counts.set(position, (counts.get(position) || 0) + 1);
    }
    const positions = [...counts.entries()]
      .map(([position, count]) => `${position} ${count}`)
      .join(', ');
    return `${players.length} player${players.length === 1 ? '' : 's'}${positions ? ` · ${positions}` : ''}`;
  }

  function draftContext(state) {
    const current = finiteNumber(state?.currentOverallPick);
    const next = finiteNumber(state?.nextUserPick);
    const until = finiteNumber(state?.picksUntilUserTurn);
    const teams = finiteNumber(state?.teamCount);
    const round = current !== null && teams && teams > 0 ? Math.floor((current - 1) / teams) + 1 : null;
    const context = [];
    context.push({
      label: 'On the clock',
      value: current === null
        ? 'Current pick unknown'
        : `Overall pick ${current}${round ? ` · Round ${round}` : ''}`,
    });
    context.push({
      label: 'Your next pick',
      value: next === null
        ? 'Draft slot unknown'
        : `Overall pick ${next}${until === null ? '' : ` · ${until} pick${until === 1 ? '' : 's'} away`}`,
    });
    context.push({ label: 'Your roster', value: rosterSummary(state?.userRoster) });
    return context;
  }

  function ledgerIssues(health) {
    const missing = positiveIntegers(health?.missingPickNumbers);
    const duplicates = positiveIntegers(health?.duplicatePickNumbers);
    const unnumbered = Math.max(0, Math.trunc(finiteNumber(health?.unnumberedPickCount) || 0));
    const issues = [];
    if (missing.length) issues.push(`Missing pick numbers: ${missing.join(', ')}`);
    if (duplicates.length) issues.push(`Duplicate pick numbers: ${duplicates.join(', ')}`);
    if (unnumbered) issues.push(`Unnumbered picks: ${unnumbered}`);
    if (health?.complete === false && !issues.length) {
      issues.push('The numbered ledger is incomplete or ambiguous.');
    }
    return issues;
  }

  function degradationMessages(response, health) {
    const messages = [];
    if (health?.fresh === false) {
      const age = finiteNumber(health?.stateAgeSeconds);
      messages.push(age === null
        ? 'Draft state freshness is unknown.'
        : `Draft state is stale by about ${Math.round(age)} seconds.`);
    }
    if (health?.teamCountSource && health.teamCountSource !== 'league') {
      const source = safeText(health.teamCountSource, 24);
      messages.push(source === 'ledger'
        ? 'Team count was inferred from the recorded ledger.'
        : 'Team count uses a default or inferred value and may be inaccurate.');
    }
    if (response?.critic?.checks?.allDraftedPlayersResolved === false) {
      messages.push('Some drafted player identities are unresolved.');
    }
    if (response?.capabilities?.injuryStatus === false) {
      messages.push('Injury status is unavailable; treat it as unknown.');
    }
    if (response?.capabilities?.externalNews === false) {
      messages.push('External news is unavailable; treat it as unknown.');
    }
    if (Array.isArray(response?.warnings)) {
      const limitedFantasyProsCoverage = new Set();
      for (const warning of response.warnings) {
        const safeWarning = safeText(warning);
        const text = safeWarning.toLowerCase();
        const structuredLedgerWarning =
          (text.includes('ledger') || text.includes('pick-number')) &&
          /(gap|duplicate|unnumbered|availability uncertain)/.test(text);
        if (structuredLedgerWarning || !safeWarning) continue;
        const coverage = safeWarning.match(
          /^FantasyPros (player catalog|injuries|news) coverage is limited by the public API$/,
        );
        if (coverage) {
          limitedFantasyProsCoverage.add(coverage[1]);
          continue;
        }
        messages.push(safeWarning);
      }
      const coverageLabels = [
        ['player catalog', 'the player catalog'],
        ['injuries', 'injuries'],
        ['news', 'news'],
      ].filter(([key]) => limitedFantasyProsCoverage.has(key)).map(([, label]) => label);
      if (coverageLabels.length) {
        const finalLabel = coverageLabels.pop();
        const joined = coverageLabels.length
          ? `${coverageLabels.join(', ')}${coverageLabels.length > 1 ? ',' : ''} and ${finalLabel}`
          : finalLabel;
        messages.push(`FantasyPros public API coverage is limited for ${joined}.`);
      }
    }
    return uniqueStrings(messages);
  }

  function recommendationCard(item, index, options = {}) {
    const confidence = percentage(item?.confidence);
    const returnProbability = percentage(item?.returnProbability);
    const scenarioProbability = percentage(item?.specialistDetails?.scenario?.survivalProbability);
    const riskSource = safeText(item?.risk?.source, 80);
    const riskUpdatedAt = isoTimestamp(item?.risk?.updatedAt);
    const suppliedRiskStatus = safeText(item?.risk?.status, 40, 'unknown').toLowerCase();
    const hasFreshAttributedInjury = options.injuryStatusAvailable === true
      && item?.risk?.fresh === true
      && item?.risk?.injuryFresh === true
      && Boolean(riskSource)
      && Boolean(riskUpdatedAt);
    const riskStatus = hasFreshAttributedInjury && INJURY_STATUSES.has(suppliedRiskStatus)
      ? suppliedRiskStatus
      : 'unknown';
    const riskLabel = riskStatus === 'unknown'
      ? 'Injury/news: unknown — not assumed healthy'
      : `Injury/news: ${riskStatus}`;
    const riskSourceLabel = item?.risk?.fresh === true && riskSource && riskUpdatedAt
      ? `Source: ${riskSource} · updated ${riskUpdatedAt}`
      : '';
    const recentNews = options.externalNewsAvailable === true
      && item?.risk?.newsFresh === true
      && Array.isArray(item?.risk?.recentNews)
      ? uniqueStrings(item.risk.recentNews.slice(0, 3).map((news) => {
        const headline = safeText(news?.headline, 240);
        const category = safeText(news?.category, 80);
        const publishedAt = isoTimestamp(news?.publishedAt);
        if (!headline || !publishedAt) return '';
        return [category, headline, publishedAt].filter(Boolean).join(' · ');
      }), 3)
      : [];
    const playerPosition = safeText(item?.player?.position, 16, 'Position unknown');
    const playerTeam = safeText(item?.player?.team, 16, 'NFL team unknown');
    const overallScore = finiteNumber(item?.overallScore);
    const rank = finiteNumber(item?.player?.rank);
    const adp = finiteNumber(item?.player?.adp);
    const byeWeek = finiteNumber(item?.player?.byeWeek);
    const tier = safeText(item?.specialistDetails?.value?.tier, 30);
    const valueParts = [
      rank === null ? '' : `Rank ${Number.isInteger(rank) ? rank : rank.toFixed(1)}`,
      adp === null ? '' : `ADP ${Number.isInteger(adp) ? adp : adp.toFixed(1)}`,
      tier ? `${tier.charAt(0).toUpperCase()}${tier.slice(1)} tier` : '',
      byeWeek === null ? '' : `Bye ${Math.trunc(byeWeek)}`,
    ].filter(Boolean);
    return {
      rankLabel: String(index + 1),
      name: safeText(item?.player?.name, 120, 'Unknown player'),
      playerMeta: `${playerPosition} · ${playerTeam}`,
      valueLabel: valueParts.join(' · ') || 'Rank, ADP, tier, and bye unavailable',
      scoreLabel: overallScore === null ? 'Score unavailable' : `Score ${overallScore.toFixed(1)}`,
      confidenceLabel: confidence
        ? `Confidence ${confidence} · uncalibrated`
        : 'Confidence unavailable · uncalibrated',
      returnProbabilityLabel: returnProbability
        ? `Estimated return ${returnProbability} · uncalibrated heuristic`
        : 'Estimated return unavailable · uncalibrated heuristic',
      scenarioProbabilityLabel: scenarioProbability
        ? `Scenario survival ${scenarioProbability} · uncalibrated simulation`
        : 'Scenario survival unavailable · uncalibrated simulation',
      rosterImpact: safeText(item?.rosterImpact, 300, 'Roster impact unavailable.'),
      riskLabel,
      riskSourceLabel,
      recentNews,
      reasoning: uniqueStrings(Array.isArray(item?.reasoning) ? item.reasoning : [], 6),
    };
  }

  function baseModel(leagueId) {
    return {
      mode: 'error',
      leagueLabel: leagueId ? `League ${leagueId}` : 'No league selected',
      statusTitle: 'Recommendations unavailable',
      statusMessage: 'Choose a recorded Yahoo league and refresh.',
      actionNotice: 'Recommendations only — this assistant never drafts players.',
      draftContext: [],
      ledgerIssues: [],
      degradations: [],
      recommendations: [],
      advisoryCritic: null,
      contingency: [],
      emptyMessage: 'No recommendation is available.',
    };
  }

  function createRecommendationViewModel(response, selectedSession = {}, options = {}) {
    const selectedLeagueId = safeText(selectedSession?.leagueId, 32);
    const model = baseModel(selectedLeagueId);
    if (!response || typeof response !== 'object' || Array.isArray(response)) return model;

    const responseLeagueId = safeText(response.leagueId, 32);
    if (selectedLeagueId && responseLeagueId !== selectedLeagueId) {
      model.statusMessage = 'The response did not match the explicitly selected Yahoo league.';
      return model;
    }

    const reportedMode = ['success', 'degraded', 'blocked', 'error'].includes(response.status)
      ? response.status
      : 'error';
    const health = response?.state?.health || {};
    const issues = ledgerIssues(health);
    const recommendationStatus = reportedMode === 'success' || reportedMode === 'degraded';
    const structuredLedgerBlocked = recommendationStatus && (
      health.complete !== true || issues.length > 0
    );
    const mode = reportedMode === 'blocked' || structuredLedgerBlocked
      ? 'blocked'
      : reportedMode;
    model.mode = mode;
    model.leagueLabel = responseLeagueId ? `League ${responseLeagueId}` : model.leagueLabel;
    model.draftContext = response.state ? draftContext(response.state) : [];
    model.ledgerIssues = issues;
    if (mode === 'blocked' && health.complete !== true && !model.ledgerIssues.length) {
      model.ledgerIssues.push('The numbered ledger is incomplete or ambiguous.');
    }
    model.degradations = degradationMessages(response, health);
    const rawMaximum = options.maxRecommendations;
    const parsedMaximum = typeof rawMaximum === 'number'
      ? rawMaximum
      : (typeof rawMaximum === 'string' && /^\d+$/.test(rawMaximum.trim())
        ? Number(rawMaximum)
        : 5);
    const requestedMaximum = Math.trunc(Number.isFinite(parsedMaximum) ? parsedMaximum : 5);
    const maximum = Math.max(1, Math.min(20, requestedMaximum));
    model.recommendations = (mode === 'success' || mode === 'degraded') && Array.isArray(response.recommendations)
      ? response.recommendations
        .slice(0, maximum)
        .map((item, index) => recommendationCard(item, index, {
          injuryStatusAvailable: response?.capabilities?.injuryStatus === true,
          externalNewsAvailable: response?.capabilities?.externalNews === true,
        }))
      : [];
    const normalizedAdvisoryCritic = mode === 'success' || mode === 'degraded'
      ? advisoryCritic(response.advisoryCritic)
      : null;
    model.advisoryCritic = normalizedAdvisoryCritic?.status === 'available' &&
      model.recommendations.length === 0
      ? null
      : normalizedAdvisoryCritic;
    model.contingency = mode === 'success' || mode === 'degraded'
      ? uniqueStrings([
        response?.contingency?.ifPrimaryUnavailable
          ? `If the primary is unavailable: ${safeText(response.contingency.ifPrimaryUnavailable, 120)}`
          : '',
        safeText(response?.contingency?.atNextTurn, 300),
      ], 3)
      : [];

    if (mode === 'success') {
      model.statusTitle = 'Recommendations ready';
      model.statusMessage = 'Based on the latest synced ledger and Yahoo league data.';
    } else if (mode === 'degraded') {
      model.statusTitle = 'Recommendations need caution';
      model.statusMessage = 'Use these picks with the data-quality cautions below.';
    } else if (mode === 'blocked') {
      model.statusTitle = 'Recommendations blocked';
      model.statusMessage = 'The authoritative draft ledger must be repaired before player availability is trustworthy.';
    } else {
      model.statusTitle = 'Recommendations unavailable';
      model.statusMessage = safeText(response.message || response.error, 300, 'The local recommendation server could not produce an answer.');
    }

    if (!model.recommendations.length) {
      model.emptyMessage = mode === 'blocked'
        ? 'Open Yahoo Results → Round by Round, then use Full rescan & repair in the recorder popup.'
        : 'No player recommendations are available. Review the status and data-quality details above.';
    } else {
      model.emptyMessage = '';
    }
    return model;
  }

  const api = { createRecommendationViewModel, safeText };
  globalScope.YahooDraftRecommendationViewModel = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
