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
  const MARKET_BADGES = new Map([
    ['value', 'Value'],
    ['sleeper-watch', 'Sleeper Watch'],
    ['fade', 'Fade'],
  ]);
  const MARKET_ACTIONS = new Map([
    ['take-now', 'Take now'],
    ['can-wait', 'Can wait'],
    ['timing-unknown', 'Timing unknown'],
  ]);
  const PLAYER_POSITIONS = new Set(['QB', 'RB', 'WR', 'TE', 'K', 'DST']);
  const BREAKOUT_OPPORTUNITY_KINDS = new Set(['touches', 'targets', 'receptions']);
  const BREAKOUT_KINDS_BY_POSITION = {
    RB: new Set(['touches']),
    WR: new Set(['targets', 'receptions']),
    TE: new Set(['targets', 'receptions']),
  };

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

  function validIsoDate(value) {
    const text = safeText(value, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return '';
    const parsed = new Date(`${text}T00:00:00Z`);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === text
      ? text
      : '';
  }

  function displayNumber(value) {
    return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
  }

  function breakoutWatch(rawBreakout, available, playerPosition) {
    if (
      available !== true ||
      !rawBreakout ||
      typeof rawBreakout !== 'object' ||
      Array.isArray(rawBreakout) ||
      rawBreakout.label !== 'Breakout Watch' ||
      rawBreakout.calibrated !== false
    ) return null;
    const source = safeText(rawBreakout.source, 80);
    const asOf = validIsoDate(rawBreakout.asOf);
    const method = safeText(rawBreakout.method, 300);
    const opportunityKind = safeText(rawBreakout.opportunityKind, 20).toLowerCase();
    const projectedPoints = finiteNumber(rawBreakout.projectedPoints);
    const projectedOpportunities = finiteNumber(rawBreakout.projectedOpportunities);
    const experienceYears = rawBreakout.experienceYears;
    const pointsPercentile = finiteNumber(rawBreakout.pointsPercentile);
    const opportunityPercentile = finiteNumber(rawBreakout.opportunityPercentile);
    const positionKinds = BREAKOUT_KINDS_BY_POSITION[playerPosition];
    if (
      !source || !asOf || !method ||
      !BREAKOUT_OPPORTUNITY_KINDS.has(opportunityKind) ||
      !positionKinds?.has(opportunityKind) ||
      projectedPoints === null || projectedPoints <= 0 || projectedPoints > 1_000 ||
      projectedOpportunities === null || projectedOpportunities < 1 || projectedOpportunities > 1_000 ||
      !Number.isInteger(experienceYears) || experienceYears < 0 || experienceYears > 30 ||
      pointsPercentile === null || pointsPercentile < 0 || pointsPercentile > 1 ||
      opportunityPercentile === null || opportunityPercentile < 0 || opportunityPercentile > 1
    ) return null;
    return {
      label: 'Breakout Watch · uncalibrated',
      detail: [
        source,
        `as of ${asOf}`,
        `${displayNumber(projectedPoints)} projected points`,
        `${displayNumber(projectedOpportunities)} ${opportunityKind}`,
        `year ${experienceYears}`,
      ].join(' · '),
      method,
    };
  }

  function fantasyProsProjection(rawProjection, playerPosition) {
    if (
      !rawProjection ||
      typeof rawProjection !== 'object' ||
      Array.isArray(rawProjection) ||
      rawProjection.source !== 'FantasyPros' ||
      !Number.isInteger(rawProjection.season) ||
      rawProjection.season < 2012 ||
      rawProjection.season > 2100 ||
      !['STD', 'HALF', 'PPR'].includes(rawProjection.scoring) ||
      typeof rawProjection.stale !== 'boolean'
    ) return null;
    const projectedPoints = finiteNumber(rawProjection.projectedPoints);
    const projectedOpportunities = finiteNumber(rawProjection.projectedOpportunities);
    const opportunityKind = safeText(rawProjection.opportunityKind, 20).toLowerCase();
    const experienceYears = finiteNumber(rawProjection.experienceYears);
    const experienceSource = safeText(rawProjection.experienceSource, 80);
    const fetchedAt = isoTimestamp(rawProjection.fetchedAt);
    const suppliedSourceAsOf = rawProjection.sourceAsOf;
    const sourceAsOf = suppliedSourceAsOf === null || suppliedSourceAsOf === undefined
      ? ''
      : isoTimestamp(suppliedSourceAsOf);
    const validKinds = BREAKOUT_KINDS_BY_POSITION[playerPosition];
    if (
      projectedPoints === null || projectedPoints < 0 || projectedPoints > 1_000 ||
      projectedOpportunities === null || projectedOpportunities < 0 || projectedOpportunities > 1_000 ||
      !validKinds?.has(opportunityKind) ||
      (experienceYears !== null && (
        !Number.isInteger(experienceYears) || experienceYears < 0 || experienceYears > 30 ||
        experienceSource !== 'Sleeper'
      )) ||
      (experienceSource && experienceYears === null) ||
      !fetchedAt ||
      (suppliedSourceAsOf !== null && suppliedSourceAsOf !== undefined && !sourceAsOf)
    ) return null;
    const timing = [
      sourceAsOf ? `source as of ${sourceAsOf}` : '',
      `fetched ${fetchedAt}`,
    ].filter(Boolean);
    return {
      label: 'FantasyPros projection evidence',
      detail: [
        `${rawProjection.season} ${rawProjection.scoring}`,
        `${displayNumber(projectedPoints)} projected points`,
        `${displayNumber(projectedOpportunities)} ${opportunityKind}`,
        experienceYears === null ? '' : `${experienceYears} years experience (${experienceSource})`,
        ...timing,
        rawProjection.stale ? 'stale cached snapshot' : 'fresh cached snapshot',
      ].filter(Boolean).join(' · '),
      caution: experienceYears === null
        ? (
          'Projection evidence only; no matching Sleeper experience was available, so this ' +
          'evidence alone does not create a Breakout Watch label.'
        )
        : 'FantasyPros projections are combined with conservatively matched Sleeper experience for Breakout Watch.',
    };
  }

  function plannedPlayer(rawPlayer) {
    if (!rawPlayer || typeof rawPlayer !== 'object' || Array.isArray(rawPlayer)) return null;
    const name = safeText(rawPlayer.name, 120);
    const position = safeText(rawPlayer.position, 16).toUpperCase();
    const team = safeText(rawPlayer.team, 16, 'NFL team unknown');
    const score = finiteNumber(rawPlayer.score);
    if (!name || !PLAYER_POSITIONS.has(position) || score === null || score < 0 || score > 100) {
      return null;
    }
    return { name, position, team };
  }

  function playerIdentity(player) {
    return `${player.name}\u0000${player.position}\u0000${player.team}`;
  }

  function nextTwoPicksPlan(rawPlan, rawRecommendations) {
    if (
      !rawPlan ||
      typeof rawPlan !== 'object' ||
      Array.isArray(rawPlan) ||
      !['ready', 'degraded'].includes(rawPlan.status) ||
      rawPlan.probabilitiesCalibrated !== false ||
      !safeText(rawPlan.method, 300) ||
      !Array.isArray(rawRecommendations) ||
      rawRecommendations.length === 0 ||
      !Array.isArray(rawPlan.fallbacksNow) ||
      !Array.isArray(rawPlan.nextUserPicks) ||
      !Array.isArray(rawPlan.combinations) ||
      !Array.isArray(rawPlan.uncertainties)
    ) return null;

    const primary = plannedPlayer(rawPlan.primaryNow);
    const deterministicPrimary = plannedPlayer({
      ...rawRecommendations[0]?.player,
      score: rawRecommendations[0]?.overallScore,
    });
    if (
      !primary ||
      !deterministicPrimary ||
      playerIdentity(primary) !== playerIdentity(deterministicPrimary)
    ) return null;

    const rawFallbacks = rawPlan.fallbacksNow;
    if (rawFallbacks.length > 2) return null;
    const fallbacks = rawFallbacks.map(plannedPlayer);
    if (fallbacks.some((player) => player === null)) return null;
    const optionIdentities = new Set([primary, ...fallbacks].map(playerIdentity));

    const rawPicks = rawPlan.nextUserPicks;
    const picksAreValid = rawPicks.length === 2 && rawPicks.every((value) => (
      typeof value === 'number' && Number.isInteger(value) && value > 0
    )) && rawPicks[1] > rawPicks[0];
    if (rawPicks.length !== 0 && !picksAreValid) return null;

    const rawCombinations = rawPlan.combinations;
    if (rawCombinations.length > 3 || (rawCombinations.length && !picksAreValid)) return null;
    const combinations = [];
    for (const rawCombination of rawCombinations) {
      if (!rawCombination || typeof rawCombination !== 'object' || Array.isArray(rawCombination)) {
        return null;
      }
      const now = plannedPlayer(rawCombination.now);
      const nextTurn = plannedPlayer(rawCombination.nextTurn);
      const probability = rawCombination.nextTurnAvailabilityProbability;
      const probabilityIsUnknown = probability === null;
      const probabilityIsValid = finiteNumber(probability) !== null && probability >= 0 && probability <= 1;
      if (
        !now || !nextTurn ||
        !optionIdentities.has(playerIdentity(now)) ||
        rawCombination.probabilityCalibrated !== false ||
        (!probabilityIsUnknown && !probabilityIsValid)
      ) return null;
      combinations.push({
        label: `${now.name} (${now.position}) \u2192 ${nextTurn.name} (${nextTurn.position})`,
        availabilityLabel: probabilityIsUnknown
          ? 'Estimated next-turn availability unknown · uncalibrated heuristic'
          : `Estimated next-turn availability ${percentage(probability)} · uncalibrated heuristic`,
        reasons: uniqueStrings(Array.isArray(rawCombination.reasons) ? rawCombination.reasons : [], 3, 240),
      });
    }
    if (rawPlan.status === 'ready' && (!picksAreValid || combinations.length === 0)) return null;

    const summary = safeText(rawPlan.summary, 400);
    if (!summary) return null;
    return {
      status: rawPlan.status,
      statusLabel: rawPlan.status === 'ready'
        ? 'Two-pick plan ready'
        : 'Two-pick plan needs caution',
      summary,
      pickLabel: picksAreValid
        ? `Your selections: ${rawPicks[0]}, then ${rawPicks[1]}`
        : 'Future selection order is unknown',
      primaryLabel: `Primary now: ${primary.name} · ${primary.position} · ${primary.team}`,
      fallbackLabels: fallbacks.map((player) => (
        `Fallback now: ${player.name} · ${player.position} · ${player.team}`
      )),
      combinations,
      uncertainties: uniqueStrings(rawPlan.uncertainties, 6, 300),
    };
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

  function marketBadges(values) {
    if (!Array.isArray(values)) return [];
    const result = [];
    for (const value of values.slice(0, 12)) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
      const code = safeText(value.code, 40).toLowerCase();
      const label = MARKET_BADGES.get(code);
      if (!label || result.some((badge) => badge.code === code)) continue;
      result.push({
        code,
        label,
        detail: safeText(value.detail, 180),
      });
      if (result.length >= 3) break;
    }
    return result;
  }

  function marketAction(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const code = safeText(value.code, 40).toLowerCase();
    const label = MARKET_ACTIONS.get(code);
    if (!label || value.calibrated !== false) return null;
    return {
      code,
      label,
      reason: safeText(value.reason, 300, 'Timing explanation unavailable.'),
    };
  }

  function marketRiskCaution(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
    return safeText(value.message, 240);
  }

  function marketSourceLabel(source) {
    if (!source || typeof source !== 'object' || Array.isArray(source)) return '';
    const name = safeText(source.name, 120);
    const season = finiteNumber(source.season);
    const asOf = isoTimestamp(source.asOf);
    if (!name) return '';
    const parts = [name];
    if (Number.isInteger(season)) parts.push(`season ${season}`);
    if (asOf) {
      const basis = safeText(source.asOfBasis, 20, 'source');
      const prefix = basis === 'imported'
        ? 'imported'
        : (basis === 'retrieved' ? 'retrieved' : 'as of');
      parts.push(`${prefix} ${asOf}`);
    }
    return parts.join(' · ');
  }

  function marketDefinition(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
    const code = safeText(value.code, 40).toLowerCase();
    const label = MARKET_BADGES.get(code);
    const description = safeText(value.description, 300);
    return label && description ? `${label}: ${description}` : '';
  }

  function marketSleeper(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const player = value.player && typeof value.player === 'object' && !Array.isArray(value.player)
      ? value.player
      : {};
    const name = safeText(player.name, 120);
    if (!name) return null;
    const position = safeText(player.position, 16, 'Position unknown');
    const team = safeText(player.team, 16, 'NFL team unknown');
    const action = marketAction(value.action);
    return {
      name,
      playerMeta: `${position} · ${team}`,
      summary: safeText(value.summary, 300, 'Market discount details unavailable.'),
      badges: marketBadges(value.badges),
      actionLabel: action?.label || '',
      actionReason: action?.reason || '',
      riskCaution: marketRiskCaution(value.riskCaution),
    };
  }

  function marketSignals(value) {
    if (
      !value ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      value.calibrated !== false ||
      !['available', 'blocked', 'unavailable'].includes(value.status)
    ) return null;
    const source = value.source && typeof value.source === 'object' && !Array.isArray(value.source)
      ? value.source
      : {};
    const sourceSeason = finiteNumber(source.season);
    const targetSeason = finiteNumber(source.targetSeason);
    const asOfBasis = safeText(source.asOfBasis, 20, 'source');
    const marketAsOf = isoTimestamp(source.asOf);
    const marketDateReady = Boolean(marketAsOf)
      && new Date(marketAsOf).getUTCFullYear() === sourceSeason
      && ['source', 'retrieved'].includes(asOfBasis);
    const sameSeasonReady = source.sameSeason === true
      && Number.isInteger(sourceSeason)
      && sourceSeason === targetSeason;
    const sourceLabel = marketSourceLabel(source);
    if (value.status === 'available' && (!sameSeasonReady || !marketDateReady || !sourceLabel)) {
      return null;
    }
    const method = safeText(value.method, 300);
    const definitions = uniqueStrings(
      (Array.isArray(value.definitions) ? value.definitions : []).map(marketDefinition),
      3,
      300,
    );
    const trust = uniqueStrings(
      (Array.isArray(value.trust) ? value.trust : []).slice(0, 4).map((item) => {
        if (!item || typeof item !== 'object' || Array.isArray(item)) return '';
        const message = safeText(item.message, 300);
        if (!message) return '';
        return `${item.passed === true ? 'Ready' : 'Blocked'}: ${message}`;
      }),
      4,
      320,
    );
    const exclusions = uniqueStrings(
      (Array.isArray(value.exclusions) ? value.exclusions : []).slice(0, 4).map((item) => (
        item && typeof item === 'object' && !Array.isArray(item)
          ? safeText(item.message, 300)
          : ''
      )),
      4,
      300,
    );
    const sleepers = value.status === 'available' && Array.isArray(value.sleeperWatch)
      ? value.sleeperWatch.slice(0, 5).map(marketSleeper).filter(Boolean)
      : [];
    return {
      status: value.status,
      message: safeText(
        value.message,
        300,
        value.status === 'available'
          ? 'Sleeper Watch is ready.'
          : `Sleeper Watch is ${value.status}.`,
      ),
      sourceLabel,
      methodLabel: method && /uncalibrated/i.test(method)
        ? method
        : 'Uncalibrated market method details unavailable.',
      scope: safeText(value.scope, 300),
      definitions,
      trust,
      exclusions,
      sleeperWatch: sleepers,
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

  function turnStatus(picksUntilUserTurn) {
    const picks = finiteNumber(picksUntilUserTurn);
    if (!Number.isInteger(picks) || picks < 0) {
      return { turnLabel: 'Turn timing unknown', turnTone: 'unknown' };
    }
    if (picks === 0) return { turnLabel: 'You are on the clock', turnTone: 'urgent' };
    if (picks === 1) return { turnLabel: 'You are next', turnTone: 'next' };
    return { turnLabel: `${picks} picks away`, turnTone: 'watch' };
  }

  function decisionBrief(state, recommendations) {
    if (!recommendations.length) return null;
    const primary = recommendations[0];
    const result = {
      ...turnStatus(state?.picksUntilUserTurn),
      primaryLabel: 'Recommended now',
      primaryName: primary.name,
      primaryMeta: primary.playerMeta,
      fallbacks: recommendations.slice(1, 3).map((candidate) => ({
        name: candidate.name,
        meta: candidate.playerMeta,
      })),
    };
    if (primary.actionLabel) result.primaryAction = primary.actionLabel;
    if (primary.badges?.length) {
      result.primaryBadges = primary.badges.map((badge) => badge.label);
    }
    return result;
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
    const enrichmentProvider = safeText(response?.enrichment?.provider, 40).toLowerCase();
    const enrichmentStatus = safeText(response?.enrichment?.status, 24).toLowerCase();
    const freshInjuryPlayers = finiteNumber(response?.enrichment?.freshInjuryPlayers);
    const noFreshFantasyProsInjuries = enrichmentProvider === 'fantasypros'
      && ['success', 'degraded'].includes(enrichmentStatus)
      && freshInjuryPlayers === 0;
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
      messages.push(noFreshFantasyProsInjuries
        ? 'No fresh FantasyPros injury record matched this player pool; missing status does not mean healthy.'
        : 'Injury status is unavailable; treat it as unknown.');
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
        ['player catalog', 'catalog'],
        ['injuries', 'injury'],
        ['news', 'news'],
      ].filter(([key]) => limitedFantasyProsCoverage.has(key)).map(([, label]) => label);
      if (coverageLabels.length) {
        if (coverageLabels.length === 1) {
          messages.push(
            `FantasyPros returned a bounded ${coverageLabels[0]} snapshot; missing records remain unknown.`,
          );
        } else {
          const finalLabel = coverageLabels.pop();
          const joined = `${coverageLabels.join(', ')}${coverageLabels.length > 1 ? ',' : ''} and ${finalLabel}`;
          messages.push(
            `FantasyPros returned bounded ${joined} snapshots; missing records remain unknown.`,
          );
        }
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
    const adp = item?.player?.adpAvailable === false
      ? null
      : finiteNumber(item?.player?.adp);
    const byeWeek = finiteNumber(item?.player?.byeWeek);
    const tier = safeText(item?.specialistDetails?.value?.tier, 30);
    const valueParts = [
      rank === null ? '' : `Rank ${Number.isInteger(rank) ? rank : rank.toFixed(1)}`,
      adp === null ? 'ADP unavailable' : `ADP ${Number.isInteger(adp) ? adp : adp.toFixed(1)}`,
      tier ? `${tier.charAt(0).toUpperCase()}${tier.slice(1)} tier` : '',
      byeWeek === null ? '' : `Bye ${Math.trunc(byeWeek)}`,
    ].filter(Boolean);
    const action = marketAction(item?.decisionSignals?.action);
    const breakout = breakoutWatch(
      item?.breakoutWatch,
      options.breakoutEvidenceAvailable,
      playerPosition,
    );
    const projection = fantasyProsProjection(item?.projectionEvidence, playerPosition);
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
      breakoutLabel: breakout?.label || '',
      breakoutDetail: breakout?.detail || '',
      breakoutMethod: breakout?.method || '',
      projectionLabel: projection?.label || '',
      projectionDetail: projection?.detail || '',
      projectionCaution: projection?.caution || '',
      reasoning: uniqueStrings(Array.isArray(item?.reasoning) ? item.reasoning : [], 6),
      badges: marketBadges(item?.decisionSignals?.badges),
      actionLabel: action?.label || '',
      actionReason: action?.reason || '',
      riskCaution: marketRiskCaution(item?.decisionSignals?.riskCaution),
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
      decisionBrief: null,
      nextTwoPicksPlan: null,
      breakoutEvidenceNotice: null,
      recommendations: [],
      marketSignals: null,
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
          breakoutEvidenceAvailable: response?.capabilities?.breakoutWatch === true,
        }))
      : [];
    model.decisionBrief = decisionBrief(response.state, model.recommendations);
    model.marketSignals = marketSignals(response.marketSignals);
    model.nextTwoPicksPlan = (mode === 'success' || mode === 'degraded') && health.complete === true
      ? nextTwoPicksPlan(response.nextTwoPicksPlan, response.recommendations)
      : null;
    const rawBreakoutSummary = response?.cockpit?.breakoutWatch;
    model.breakoutEvidenceNotice = (mode === 'success' || mode === 'degraded') &&
      rawBreakoutSummary &&
      typeof rawBreakoutSummary === 'object' &&
      !Array.isArray(rawBreakoutSummary) &&
      rawBreakoutSummary.status === 'unavailable' &&
      rawBreakoutSummary.calibrated === false
      ? safeText(
        rawBreakoutSummary.message,
        400,
        'Breakout evidence is unavailable; ordinary recommendations remain usable.',
      )
      : null;
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
