const test = require('node:test');
const assert = require('node:assert/strict');

const { createRecommendationViewModel } = require('../recommendation-view-model.js');

function candidate(index, overrides = {}) {
  return {
    player: {
      name: `Player ${index}`,
      position: index % 2 ? 'WR' : 'RB',
      team: 'SEA',
      rank: index,
      adp: index + 3,
      byeWeek: 9,
    },
    overallScore: 90 - index,
    confidence: 0.81,
    confidenceCalibrated: false,
    returnProbability: 0.37,
    rosterImpact: 'fills a starting WR slot',
    reasoning: ['starter value', 'injury/news status is unknown, not assumed healthy'],
    risk: { status: 'unknown', fresh: false },
    specialistDetails: {
      scenario: { survivalProbability: 0.42, calibrated: false },
      value: { tier: 'starter' },
    },
    ...overrides,
  };
}

function response(overrides = {}) {
  const recommendations = Array.from({ length: 7 }, (_, index) => candidate(index + 1));
  return {
    status: 'degraded',
    leagueId: '10462193',
    generatedAt: '2026-08-31T22:00:00.000Z',
    state: {
      currentOverallPick: 25,
      nextUserPick: 31,
      picksUntilUserTurn: 6,
      teamCount: 12,
      userRoster: [{ position: 'RB' }, { position: 'WR' }],
      health: {
        complete: true,
        fresh: false,
        teamCountSource: 'ledger',
        stateAgeSeconds: 181,
        missingPickNumbers: [],
        duplicatePickNumbers: [],
        unnumberedPickCount: 0,
      },
    },
    capabilities: { injuryStatus: false, externalNews: false },
    critic: {
      passed: false,
      checks: { allDraftedPlayersResolved: false, stateFresh: false },
    },
    recommendations,
    contingency: {
      ifPrimaryUnavailable: 'Player 2',
      atNextTurn: 'Re-run after every pick',
    },
    warnings: ['Yahoo league roster positions are unavailable; using 1QB defaults'],
    ...overrides,
  };
}

test('builds a bounded recommendation view with roster, round, degradation, and risk context', () => {
  const model = createRecommendationViewModel(response(), { leagueId: '10462193' });

  assert.equal(model.mode, 'degraded');
  assert.equal(model.leagueLabel, 'League 10462193');
  assert.deepEqual(model.draftContext, [
    { label: 'On the clock', value: 'Overall pick 25 · Round 3' },
    { label: 'Your next pick', value: 'Overall pick 31 · 6 picks away' },
    { label: 'Your roster', value: '2 players · RB 1, WR 1' },
  ]);
  assert.equal(model.recommendations.length, 5);
  assert.equal(model.recommendations[0].valueLabel, 'Rank 1 · ADP 4 · Starter tier · Bye 9');
  assert.match(model.recommendations[0].confidenceLabel, /uncalibrated/);
  assert.match(model.recommendations[0].returnProbabilityLabel, /uncalibrated heuristic/);
  assert.match(model.recommendations[0].scenarioProbabilityLabel, /uncalibrated simulation/);
  assert.equal(model.recommendations[0].riskLabel, 'Injury/news: unknown — not assumed healthy');
  assert.deepEqual(model.decisionBrief, {
    turnLabel: '6 picks away',
    turnTone: 'watch',
    primaryLabel: 'Recommended now',
    primaryName: 'Player 1',
    primaryMeta: 'WR · SEA',
    fallbacks: [
      { name: 'Player 2', meta: 'RB · SEA' },
      { name: 'Player 3', meta: 'WR · SEA' },
    ],
  });
  assert.deepEqual(model.degradations, [
    'Draft state is stale by about 181 seconds.',
    'Team count was inferred from the recorded ledger.',
    'Some drafted player identities are unresolved.',
    'Injury status is unavailable; treat it as unknown.',
    'External news is unavailable; treat it as unknown.',
    'Yahoo league roster positions are unavailable; using 1QB defaults',
  ]);
  assert.equal(model.actionNotice, 'Recommendations only — this assistant never drafts players.');
});

test('makes turn urgency explicit without inventing a draft clock', () => {
  const cases = [
    { picksUntilUserTurn: 0, turnLabel: 'You are on the clock', turnTone: 'urgent' },
    { picksUntilUserTurn: 1, turnLabel: 'You are next', turnTone: 'next' },
    { picksUntilUserTurn: 3, turnLabel: '3 picks away', turnTone: 'watch' },
    { picksUntilUserTurn: null, turnLabel: 'Turn timing unknown', turnTone: 'unknown' },
  ];

  for (const expected of cases) {
    const base = response();
    const model = createRecommendationViewModel({
      ...base,
      state: { ...base.state, picksUntilUserTurn: expected.picksUntilUserTurn },
    }, { leagueId: '10462193' });
    assert.equal(model.decisionBrief.turnLabel, expected.turnLabel);
    assert.equal(model.decisionBrief.turnTone, expected.turnTone);
  }
});

test('shows a bounded deterministic two-pick plan with uncalibrated availability', () => {
  const model = createRecommendationViewModel(response({
    nextTwoPicksPlan: {
      status: 'ready',
      method: 'bounded deterministic candidate-pair scoring',
      probabilitiesCalibrated: false,
      primaryNow: { name: 'Player 1', position: 'WR', team: 'SEA', score: 89 },
      fallbacksNow: [{ name: 'Player 2', position: 'RB', team: 'SEA', score: 88 }],
      nextUserPicks: [31, 42],
      combinations: [{
        now: { name: 'Player 1', position: 'WR', team: 'SEA', score: 89 },
        nextTurn: { name: 'Player 4', position: 'RB', team: 'SEA', score: 86 },
        positions: ['WR', 'RB'],
        combinedScore: 82,
        nextTurnAvailabilityProbability: 0.63,
        probabilityCalibrated: false,
        reasons: ['Spreads positions.', 'Uses actual ADP only.'],
      }],
      uncertainties: [],
      summary: 'Use the primary now and re-run after every pick.',
    },
  }), { leagueId: '10462193' });

  assert.deepEqual(model.nextTwoPicksPlan, {
    status: 'ready',
    statusLabel: 'Two-pick plan ready',
    summary: 'Use the primary now and re-run after every pick.',
    pickLabel: 'Your selections: 31, then 42',
    primaryLabel: 'Primary now: Player 1 · WR · SEA',
    fallbackLabels: ['Fallback now: Player 2 · RB · SEA'],
    combinations: [{
      label: 'Player 1 (WR) → Player 4 (RB)',
      availabilityLabel: 'Estimated next-turn availability 63% · uncalibrated heuristic',
      reasons: ['Spreads positions.', 'Uses actual ADP only.'],
    }],
    uncertainties: [],
  });
});

test('omits an inconsistent two-pick plan instead of overriding deterministic order', () => {
  const model = createRecommendationViewModel(response({
    nextTwoPicksPlan: {
      status: 'ready',
      probabilitiesCalibrated: false,
      primaryNow: { name: 'Contradictory Player', position: 'QB', team: 'BUF', score: 99 },
      fallbacksNow: [],
      nextUserPicks: [31, 42],
      combinations: [],
      uncertainties: [],
      summary: 'Override the deterministic primary.',
    },
  }), { leagueId: '10462193' });

  assert.equal(model.nextTwoPicksPlan, null);
});

test('shows Breakout Watch only for complete explicit uncalibrated evidence', () => {
  const breakout = {
    label: 'Breakout Watch',
    method: 'fresh same-source position cohort',
    source: 'Example Projections',
    asOf: '2026-08-20',
    projectedPoints: 210,
    projectedOpportunities: 125,
    opportunityKind: 'targets',
    experienceYears: 2,
    pointsPercentile: 0.8,
    opportunityPercentile: 0.8,
    calibrated: false,
  };
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: false, breakoutWatch: true },
    recommendations: [candidate(1, { breakoutWatch: breakout })],
  }), { leagueId: '10462193' });

  assert.equal(model.recommendations[0].breakoutLabel, 'Breakout Watch · uncalibrated');
  assert.equal(
    model.recommendations[0].breakoutDetail,
    'Example Projections · as of 2026-08-20 · 210 projected points · 125 targets · year 2',
  );
  assert.equal(model.recommendations[0].breakoutMethod, 'fresh same-source position cohort');

  const invalid = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: false, breakoutWatch: true },
    recommendations: [candidate(1, {
      breakoutWatch: { ...breakout, calibrated: true },
      risk: { recentNews: [{ headline: 'Breakout season incoming' }] },
    })],
  }), { leagueId: '10462193' });
  assert.equal(invalid.recommendations[0].breakoutLabel, '');

  const wrongOpportunityKind = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: false, breakoutWatch: true },
    recommendations: [candidate(1, {
      player: { ...candidate(1).player, position: 'WR' },
      breakoutWatch: { ...breakout, opportunityKind: 'touches' },
    })],
  }), { leagueId: '10462193' });
  assert.equal(wrongOpportunityKind.recommendations[0].breakoutLabel, '');
});

test('shows allowlisted FantasyPros projections as evidence, never as Breakout Watch', () => {
  const projectionEvidence = {
    source: 'FantasyPros',
    season: 2026,
    scoring: 'PPR',
    sourceAsOf: null,
    fetchedAt: '2026-08-28T16:00:00Z',
    stale: false,
    projectedPoints: 294.5,
    projectedOpportunities: 124.25,
    opportunityKind: 'receptions',
    rawUrl: 'https://evil.test/?token=secret',
  };
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: false, breakoutWatch: false },
    recommendations: [candidate(1, { projectionEvidence })],
  }), { leagueId: '10462193' });

  assert.equal(model.recommendations[0].projectionLabel, 'FantasyPros projection evidence');
  assert.equal(
    model.recommendations[0].projectionDetail,
    '2026 PPR · 294.5 projected points · 124.25 receptions · fetched 2026-08-28T16:00:00Z · fresh cached snapshot',
  );
  assert.match(model.recommendations[0].projectionCaution, /does not create a Breakout Watch label/i);
  assert.equal(model.recommendations[0].breakoutLabel, '');
  assert.doesNotMatch(JSON.stringify(model.recommendations[0]), /evil\.test|secret/);

  const matchedExperience = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: false, breakoutWatch: true },
    recommendations: [candidate(1, {
      projectionEvidence: {
        ...projectionEvidence,
        experienceYears: 2,
        experienceSource: 'Sleeper',
      },
    })],
  }), { leagueId: '10462193' });
  assert.match(matchedExperience.recommendations[0].projectionDetail, /2 years experience \(Sleeper\)/);
  assert.match(matchedExperience.recommendations[0].projectionCaution, /combined.*Sleeper/i);

  const malformed = [
    { ...projectionEvidence, source: 'FantasyPros <script>' },
    { ...projectionEvidence, season: true },
    { ...projectionEvidence, scoring: 'CUSTOM' },
    { ...projectionEvidence, fetchedAt: 'https://evil.test/?token=secret' },
    { ...projectionEvidence, stale: 'false' },
    { ...projectionEvidence, projectedPoints: Infinity },
    { ...projectionEvidence, opportunityKind: 'touches' },
  ];
  for (const value of malformed) {
    const invalid = createRecommendationViewModel(response({
      recommendations: [candidate(1, { projectionEvidence: value })],
    }), { leagueId: '10462193' });
    assert.equal(invalid.recommendations[0].projectionLabel, '');
  }
});

test('explains unavailable breakout evidence without degrading ordinary recommendations', () => {
  const model = createRecommendationViewModel(response({
    cockpit: {
      breakoutWatch: {
        status: 'unavailable',
        calibrated: false,
        message: 'Breakout evidence is unavailable: import fresh sourced projections.',
      },
    },
  }), { leagueId: '10462193' });

  assert.equal(
    model.breakoutEvidenceNotice,
    'Breakout evidence is unavailable: import fresh sourced projections.',
  );
  assert.equal(model.mode, 'degraded');
});

test('groups repeated FantasyPros public coverage warnings into one readable disclosure', () => {
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: true },
    enrichment: {
      provider: 'FantasyPros',
      status: 'degraded',
      requestedPlayers: 250,
      freshInjuryPlayers: 0,
      freshNewsPlayers: 19,
    },
    warnings: [
      'FantasyPros player catalog coverage is limited by the public API',
      'FantasyPros injuries coverage is limited by the public API',
      'FantasyPros news coverage is limited by the public API',
    ],
  }), { leagueId: '10462193' });

  assert.deepEqual(model.degradations, [
    'Draft state is stale by about 181 seconds.',
    'Team count was inferred from the recorded ledger.',
    'Some drafted player identities are unresolved.',
    'No fresh FantasyPros injury record matched this player pool; missing status does not mean healthy.',
    'FantasyPros returned bounded catalog, injury, and news snapshots; missing records remain unknown.',
  ]);
});

test('renders one FantasyPros coverage marker as a singular bounded snapshot', () => {
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: true, externalNews: true },
    enrichment: {
      provider: 'FantasyPros',
      status: 'degraded',
      requestedPlayers: 250,
      freshInjuryPlayers: 1,
    },
    warnings: ['FantasyPros injuries coverage is limited by the public API'],
  }), { leagueId: '10462193' });

  assert.equal(
    model.degradations.at(-1),
    'FantasyPros returned a bounded injury snapshot; missing records remain unknown.',
  );
});

test('keeps generic injury wording when FantasyPros enrichment is unavailable', () => {
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: true },
    enrichment: {
      provider: 'FantasyPros',
      status: 'unavailable',
      requestedPlayers: 250,
      freshInjuryPlayers: 0,
    },
    warnings: [],
  }), { leagueId: '10462193' });

  assert.ok(model.degradations.includes('Injury status is unavailable; treat it as unknown.'));
  assert.ok(!model.degradations.some((message) => message.startsWith('No fresh FantasyPros')));
});

test('lets the shared dashboard request a bounded twenty-card board', () => {
  const many = Array.from({ length: 30 }, (_, index) => candidate(index + 1));
  const model = createRecommendationViewModel(
    response({ recommendations: many }),
    { leagueId: '10462193' },
    { maxRecommendations: 999 },
  );

  assert.equal(model.recommendations.length, 20);
  assert.equal(createRecommendationViewModel(
    response({ recommendations: many }),
    { leagueId: '10462193' },
    { maxRecommendations: '20' },
  ).recommendations.length, 20);
});

test('never presents a player as healthy when injury capability is unavailable', () => {
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: false },
    recommendations: [candidate(1, { risk: { status: 'healthy', fresh: true } })],
  }), { leagueId: '10462193' });

  assert.equal(model.recommendations[0].riskLabel, 'Injury/news: unknown — not assumed healthy');
});

test('requires fresh attributed per-player evidence before showing an injury status', () => {
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: true, externalNews: false },
    recommendations: [
      candidate(1, {
        risk: {
          status: 'questionable',
          source: 'FantasyPros',
          updatedAt: '2026-09-01T22:15:00Z',
          fresh: true,
          injuryFresh: true,
        },
      }),
      candidate(2, {
        risk: {
          status: 'healthy',
          source: 'FantasyPros',
          updatedAt: '2026-08-01T22:15:00Z',
          fresh: false,
          injuryFresh: false,
        },
      }),
      candidate(3, {
        risk: {
          status: 'definitely healthy',
          source: 'FantasyPros',
          updatedAt: '2026-09-01T22:15:00Z',
          fresh: true,
          injuryFresh: true,
        },
      }),
    ],
  }), { leagueId: '10462193' });

  assert.equal(model.recommendations[0].riskLabel, 'Injury/news: questionable');
  assert.equal(model.recommendations[1].riskLabel, 'Injury/news: unknown — not assumed healthy');
  assert.equal(model.recommendations[2].riskLabel, 'Injury/news: unknown — not assumed healthy');
});

test('shows allowlisted FantasyPros source and recent headlines without changing unknown status', () => {
  const model = createRecommendationViewModel(response({
    capabilities: { injuryStatus: false, externalNews: true },
    recommendations: [candidate(1, {
      risk: {
        status: 'unknown',
        source: 'FantasyPros',
        updatedAt: '2026-09-01T22:15:00Z',
        fresh: true,
        newsFresh: true,
        recentNews: [
          {
            headline: 'Returns to full team drills',
            category: 'Injuries',
            publishedAt: '2026-09-01T21:00:00Z',
          },
          { headline: '<img src=x onerror=alert(1)>', publishedAt: 'invalid' },
        ],
      },
    })],
  }), { leagueId: '10462193' });

  assert.equal(model.recommendations[0].riskLabel, 'Injury/news: unknown — not assumed healthy');
  assert.equal(
    model.recommendations[0].riskSourceLabel,
    'Source: FantasyPros · updated 2026-09-01T22:15:00Z',
  );
  assert.deepEqual(model.recommendations[0].recentNews, [
    'Injuries · Returns to full team drills · 2026-09-01T21:00:00Z',
  ]);
});

test('does not coerce null, empty, or boolean response fields into picks or probabilities', () => {
  const model = createRecommendationViewModel(response({
    state: {
      currentOverallPick: null,
      nextUserPick: '',
      picksUntilUserTurn: false,
      teamCount: null,
      userRoster: [],
      health: {
        complete: true,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [],
        duplicatePickNumbers: [],
        unnumberedPickCount: null,
      },
    },
    recommendations: [candidate(1, {
      confidence: null,
      returnProbability: '',
      specialistDetails: {
        value: { tier: 'starter' },
        scenario: { survivalProbability: false },
      },
    })],
  }), { leagueId: '10462193' });

  assert.equal(model.draftContext[0].value, 'Current pick unknown');
  assert.equal(model.draftContext[1].value, 'Draft slot unknown');
  assert.equal(model.recommendations[0].confidenceLabel, 'Confidence unavailable · uncalibrated');
  assert.equal(model.recommendations[0].returnProbabilityLabel, 'Estimated return unavailable · uncalibrated heuristic');
  assert.equal(model.recommendations[0].scenarioProbabilityLabel, 'Scenario survival unavailable · uncalibrated simulation');
  assert.deepEqual(model.ledgerIssues, []);

  const ledgerModel = createRecommendationViewModel(response({
    status: 'success',
    state: {
      currentOverallPick: 7,
      nextUserPick: 11,
      picksUntilUserTurn: 4,
      teamCount: 10,
      userRoster: [],
      health: {
        complete: true,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [null, '', false, 3],
        duplicatePickNumbers: [],
        unnumberedPickCount: null,
      },
    },
  }), { leagueId: '10462193' });
  assert.equal(ledgerModel.mode, 'blocked');
  assert.deepEqual(ledgerModel.ledgerIssues, ['Missing pick numbers: 3']);
});

test('surfaces exact ledger blockers and does not invent recommendations', () => {
  const model = createRecommendationViewModel(response({
    status: 'blocked',
    recommendations: [],
    warnings: [
      'Recommendation blocked because a pick-number gap makes availability uncertain',
      'Keep the complete Yahoo ledger visible.',
    ],
    state: {
      currentOverallPick: 9,
      nextUserPick: null,
      picksUntilUserTurn: null,
      teamCount: 10,
      userRoster: [],
      health: {
        complete: false,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [3, 7],
        duplicatePickNumbers: [5],
        unnumberedPickCount: 2,
      },
    },
  }), { leagueId: '10462193' });

  assert.equal(model.mode, 'blocked');
  assert.deepEqual(model.ledgerIssues, [
    'Missing pick numbers: 3, 7',
    'Duplicate pick numbers: 5',
    'Unnumbered picks: 2',
  ]);
  assert.equal(model.recommendations.length, 0);
  assert.equal(model.degradations.includes('Keep the complete Yahoo ledger visible.'), true);
  assert.equal(model.degradations.some((message) => /pick-number gap/.test(message)), false);
  assert.match(model.emptyMessage, /Full rescan & repair/);
});

test('structured ledger anomalies override contradictory success candidates', () => {
  const cases = [
    {
      name: 'incomplete flag',
      health: {
        complete: false,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [],
        duplicatePickNumbers: [],
        unnumberedPickCount: 0,
      },
    },
    {
      name: 'missing number',
      health: {
        complete: true,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [3],
        duplicatePickNumbers: [],
        unnumberedPickCount: 0,
      },
    },
    {
      name: 'duplicate number',
      health: {
        complete: true,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [],
        duplicatePickNumbers: [5],
        unnumberedPickCount: 0,
      },
    },
    {
      name: 'unnumbered pick',
      health: {
        complete: true,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [],
        duplicatePickNumbers: [],
        unnumberedPickCount: 1,
      },
    },
  ];

  for (const { name, health } of cases) {
    const model = createRecommendationViewModel(response({
      status: 'success',
      state: {
        currentOverallPick: 7,
        nextUserPick: 11,
        picksUntilUserTurn: 4,
        teamCount: 10,
        userRoster: [],
        health,
      },
      recommendations: [candidate(1)],
      contingency: {
        ifPrimaryUnavailable: 'Contradictory Player 2',
        atNextTurn: 'Draft a contradictory candidate',
      },
    }), { leagueId: '10462193' });

    assert.equal(model.mode, 'blocked', name);
    assert.deepEqual(model.recommendations, [], name);
    assert.equal(model.decisionBrief, null, name);
    assert.deepEqual(model.contingency, [], name);
    assert.match(model.emptyMessage, /Full rescan & repair/, name);
  }
});

test('reported blocked status suppresses candidates even when health says complete', () => {
  const model = createRecommendationViewModel(response({
    status: 'blocked',
    state: {
      currentOverallPick: 7,
      nextUserPick: 11,
      picksUntilUserTurn: 4,
      teamCount: 10,
      userRoster: [],
      health: {
        complete: true,
        fresh: true,
        teamCountSource: 'league',
        missingPickNumbers: [],
        duplicatePickNumbers: [],
        unnumberedPickCount: 0,
      },
    },
    recommendations: [candidate(1)],
  }), { leagueId: '10462193' });

  assert.equal(model.mode, 'blocked');
  assert.deepEqual(model.recommendations, []);
});

test('sanitizes untrusted response text and caps arrays', () => {
  const malicious = '<img src=x onerror="globalThis.pwned=true">';
  const model = createRecommendationViewModel(response({
    recommendations: [candidate(1, {
      player: { name: malicious, position: 'WR', team: 'SEA' },
      reasoning: Array.from({ length: 20 }, (_, index) => `${malicious} ${index}`),
      rosterImpact: malicious.repeat(100),
    })],
    warnings: Array.from({ length: 50 }, () => malicious),
  }), { leagueId: '10462193' });

  assert.equal(model.recommendations[0].name, malicious);
  assert.equal(model.recommendations[0].reasoning.length, 6);
  assert.ok(model.recommendations[0].rosterImpact.length <= 300);
  assert.ok(model.degradations.length <= 12);
});

test('sanitizes and bounds an available advisory critic without changing deterministic cards', () => {
  const malicious = '<img src=x onerror="globalThis.pwned=true">';
  const deterministicRecommendations = [candidate(1), candidate(2), candidate(3)];
  const model = createRecommendationViewModel(response({
    recommendations: deterministicRecommendations,
    advisoryCritic: {
      status: 'available',
      provider: 'Databricks',
      model: malicious.repeat(20),
      advisoryOnly: true,
      summary: malicious.repeat(100),
      cautions: Array.from({ length: 20 }, (_, index) => `${index} ${malicious.repeat(20)}`),
      cached: true,
      latencyMs: 999_999,
    },
  }), { leagueId: '10462193' });

  assert.deepEqual(model.recommendations.map((item) => item.name), [
    'Player 1',
    'Player 2',
    'Player 3',
  ]);
  assert.deepEqual(model.recommendations.map((item) => item.scoreLabel), [
    'Score 89.0',
    'Score 88.0',
    'Score 87.0',
  ]);
  assert.equal(model.advisoryCritic.status, 'available');
  assert.equal(model.advisoryCritic.provider, 'Databricks');
  assert.equal(model.advisoryCritic.advisoryOnly, true);
  assert.equal(model.advisoryCritic.cached, true);
  assert.equal(model.advisoryCritic.latencyMs, 60_000);
  assert.ok(model.advisoryCritic.model.length <= 120);
  assert.ok(model.advisoryCritic.summary.length <= 600);
  assert.equal(model.advisoryCritic.cautions.length, 6);
  assert.ok(model.advisoryCritic.cautions.every((caution) => caution.length <= 240));
});

test('keeps an unavailable advisory critic generic and omits absent or malformed critics', () => {
  const malicious = '<svg onload="globalThis.pwned=true">';
  const unavailable = createRecommendationViewModel(response({
    advisoryCritic: {
      status: 'unavailable',
      provider: 'Databricks',
      model: 'unit-test-fast-model',
      advisoryOnly: true,
      cached: false,
      latencyMs: -20,
      unavailableReason: {
        code: 'TIMEOUT<script>',
        message: malicious.repeat(30),
      },
    },
  }), { leagueId: '10462193' });

  assert.equal(unavailable.advisoryCritic.status, 'unavailable');
  assert.equal(unavailable.advisoryCritic.unavailableReason.code, 'timeout_script_');
  assert.ok(unavailable.advisoryCritic.unavailableReason.message.startsWith(malicious));
  assert.ok(unavailable.advisoryCritic.unavailableReason.message.length <= 300);
  assert.equal(unavailable.advisoryCritic.latencyMs, 0);

  const absent = createRecommendationViewModel(response(), { leagueId: '10462193' });
  const malformed = createRecommendationViewModel(response({
    advisoryCritic: {
      status: 'available',
      advisoryOnly: false,
      summary: 'This must not be presented as authoritative.',
    },
  }), { leagueId: '10462193' });
  assert.equal(absent.advisoryCritic, null);
  assert.equal(malformed.advisoryCritic, null);
});

test('does not show available AI advice without a deterministic recommendation board', () => {
  const model = createRecommendationViewModel(response({
    recommendations: [],
    advisoryCritic: {
      status: 'available',
      provider: 'Databricks',
      model: 'unit-test-fast-model',
      advisoryOnly: true,
      summary: 'Draft someone who is not on the deterministic board.',
      cautions: [],
      cached: false,
      latencyMs: 10,
    },
  }), { leagueId: '10462193' });

  assert.deepEqual(model.recommendations, []);
  assert.equal(model.advisoryCritic, null);
});

test('builds bounded market badges, action guidance, and sleeper-watch trust details', () => {
  const malicious = '<img src=x onerror="globalThis.pwned=true">';
  const marketCandidate = candidate(1, {
    decisionSignals: {
      badges: [
        { code: 'value', label: 'Value', detail: '12 picks past real ADP' },
        { code: 'sleeper-watch', label: 'Sleeper Watch', detail: 'Ranked 18 picks ahead of real ADP' },
        { code: 'breakout', label: 'Breakout', detail: 'must be ignored' },
      ],
      action: {
        code: 'take-now',
        label: 'Take now',
        reason: 'Uncalibrated ADP heuristic estimates a 31% chance of reaching pick 31.',
        calibrated: false,
      },
      riskCaution: { message: 'Fresh questionable status from FantasyPros.' },
    },
  });
  const sleeperWatch = Array.from({ length: 9 }, (_, index) => ({
    player: { name: `${malicious} ${index}`, position: 'WR', team: 'SEA' },
    summary: `Ranked ${20 + index} picks ahead of real ADP.`,
    badges: [{ code: 'sleeper-watch', label: 'Sleeper Watch', detail: 'Market discount' }],
    action: {
      code: index % 2 ? 'can-wait' : 'take-now',
      label: index % 2 ? 'Can wait' : 'Take now',
      reason: `Uncalibrated timing reason ${index}`,
      calibrated: false,
    },
    riskCaution: index === 0 ? { message: malicious } : null,
  }));
  const model = createRecommendationViewModel(response({
    recommendations: [marketCandidate],
    marketSignals: {
      status: 'available',
      calibrated: false,
      method: 'Uncalibrated deterministic rank-versus-ADP market heuristic.',
      scope: 'Counts cover only the bounded ranking frontier.',
      source: {
        name: 'DraftSheets 2026',
        season: 2026,
        targetSeason: 2026,
        sameSeason: true,
        asOf: '2026-09-01',
      },
      definitions: [
        { code: 'value', label: 'Value', description: 'At least one league round past real ADP.' },
        { code: 'sleeper-watch', label: 'Sleeper Watch', description: 'Round 7+ and rank beats real ADP by a league round.' },
        { code: 'fade', label: 'Fade', description: 'Rank trails real ADP by a league round.' },
        { code: 'breakout', label: 'Breakout', description: 'must be ignored' },
      ],
      trust: [
        { code: 'ledger-complete', passed: true, message: 'Authoritative ledger complete.' },
        { code: 'drafted-identities-resolved', passed: true, message: 'Drafted identities resolved.' },
      ],
      exclusions: [
        { code: 'drafted', count: 24, message: '24 drafted players excluded.' },
        { code: 'no-real-adp', count: 3, message: '3 players have no real ADP.' },
      ],
      sleeperWatch,
    },
  }), { leagueId: '10462193' });

  assert.deepEqual(model.recommendations[0].badges, [
    { code: 'value', label: 'Value', detail: '12 picks past real ADP' },
    { code: 'sleeper-watch', label: 'Sleeper Watch', detail: 'Ranked 18 picks ahead of real ADP' },
  ]);
  assert.equal(model.recommendations[0].actionLabel, 'Take now');
  assert.match(model.recommendations[0].actionReason, /31% chance/);
  assert.match(model.recommendations[0].riskCaution, /questionable/);
  assert.equal(model.decisionBrief.primaryAction, 'Take now');
  assert.deepEqual(model.decisionBrief.primaryBadges, ['Value', 'Sleeper Watch']);
  assert.equal(model.marketSignals.status, 'available');
  assert.match(model.marketSignals.sourceLabel, /DraftSheets 2026 · season 2026 · as of 2026-09-01/);
  assert.match(model.marketSignals.methodLabel, /uncalibrated/i);
  assert.equal(model.marketSignals.sleeperWatch.length, 5);
  assert.equal(model.marketSignals.definitions.length, 3);
  assert.equal(model.marketSignals.definitions.some((item) => /breakout/i.test(item)), false);
  assert.equal(model.marketSignals.exclusions.length, 2);
  assert.ok(model.marketSignals.sleeperWatch[0].name.startsWith(malicious));
});

test('fails closed when market signals are malformed or unavailable', () => {
  const malformed = createRecommendationViewModel(response({
    marketSignals: {
      status: 'available',
      calibrated: true,
      source: { sameSeason: false },
      sleeperWatch: [{ player: { name: 'Must not render', position: 'WR' } }],
    },
  }), { leagueId: '10462193' });
  assert.equal(malformed.marketSignals, null);

  const importedTimestampOnly = createRecommendationViewModel(response({
    marketSignals: {
      status: 'available',
      calibrated: false,
      method: 'Uncalibrated market heuristic.',
      source: {
        name: 'Undated CSV',
        season: 2026,
        targetSeason: 2026,
        sameSeason: true,
        asOf: '2026-09-01T12:00:00Z',
        asOfBasis: 'imported',
      },
      sleeperWatch: [{ player: { name: 'Must not render', position: 'WR' } }],
    },
  }), { leagueId: '10462193' });
  assert.equal(importedTimestampOnly.marketSignals, null);

  const mismatchedSourceDate = createRecommendationViewModel(response({
    marketSignals: {
      status: 'available',
      calibrated: false,
      method: 'Uncalibrated market heuristic.',
      source: {
        name: 'Wrong-year source',
        season: 2026,
        targetSeason: 2026,
        sameSeason: true,
        asOf: '2025-09-01',
      },
      sleeperWatch: [{ player: { name: 'Must not render', position: 'WR' } }],
    },
  }), { leagueId: '10462193' });
  assert.equal(mismatchedSourceDate.marketSignals, null);

  const unavailable = createRecommendationViewModel(response({
    marketSignals: {
      status: 'unavailable',
      calibrated: false,
      method: 'Uncalibrated deterministic rank-versus-ADP market heuristic.',
      message: 'Same-season real ADP is unavailable.',
      source: {
        name: 'Old rankings',
        season: 2025,
        targetSeason: 2026,
        sameSeason: false,
        asOf: '2025-09-01',
      },
      definitions: [],
      trust: [],
      exclusions: [],
      sleeperWatch: [],
    },
  }), { leagueId: '10462193' });
  assert.equal(unavailable.marketSignals.status, 'unavailable');
  assert.deepEqual(unavailable.marketSignals.sleeperWatch, []);
  assert.match(unavailable.marketSignals.message, /unavailable/);
});
