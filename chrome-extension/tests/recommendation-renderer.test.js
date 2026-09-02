const test = require('node:test');
const assert = require('node:assert/strict');

const { createRecommendationViewModel } = require('../recommendation-view-model.js');
const { renderRecommendationView } = require('../recommendation-renderer.js');

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.className = '';
    this.hidden = false;
    this._textContent = '';
  }

  set textContent(value) { this._textContent = String(value); }
  get textContent() { return this._textContent + this.children.map((child) => child.textContent).join(''); }
  set innerHTML(_value) { throw new Error('unsafe innerHTML write'); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = [...children]; this._textContent = ''; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
}

const fakeDocument = { createElement: (tagName) => new FakeElement(tagName) };

function findAll(root, predicate) {
  const result = predicate(root) ? [root] : [];
  for (const child of root.children) result.push(...findAll(child, predicate));
  return result;
}

test('renders all server-provided content as inert text with accessible structure', () => {
  globalThis.pwned = false;
  const malicious = '<img src=x onerror="globalThis.pwned=true">';
  const root = new FakeElement('main');

  renderRecommendationView(root, {
    mode: 'degraded',
    leagueLabel: 'League 123',
    statusTitle: 'Recommendations need caution',
    statusMessage: malicious,
    actionNotice: 'Recommendations only — this assistant never drafts players.',
    draftContext: [{ label: 'On the clock', value: 'Overall pick 7 · Round 1' }],
    ledgerIssues: [],
    degradations: [malicious],
    decisionBrief: {
      turnLabel: 'You are next',
      turnTone: 'next',
      primaryLabel: 'Recommended now',
      primaryName: malicious,
      primaryMeta: 'WR · SEA',
      fallbacks: [{ name: 'Safe fallback', meta: 'RB · DEN' }],
    },
    nextTwoPicksPlan: {
      status: 'degraded',
      statusLabel: 'Two-pick plan needs caution',
      summary: malicious,
      pickLabel: 'Your selections: 7, then 18',
      primaryLabel: `Primary now: ${malicious} · WR · SEA`,
      fallbackLabels: ['Fallback now: Safe fallback · RB · DEN'],
      combinations: [{
        label: `${malicious} (WR) → Safe fallback (RB)`,
        availabilityLabel: 'Estimated next-turn availability 63% · uncalibrated heuristic',
        reasons: [malicious],
      }],
      uncertainties: [malicious],
    },
    breakoutEvidenceNotice: 'Breakout evidence is unavailable.',
    recommendations: [{
      rankLabel: '1',
      name: malicious,
      playerMeta: 'WR · SEA',
      valueLabel: 'Rank 1 · ADP 4 · Starter tier · Bye 9',
      scoreLabel: 'Score 88.2',
      confidenceLabel: 'Confidence 81% · uncalibrated',
      returnProbabilityLabel: 'Estimated return 37% · uncalibrated heuristic',
      scenarioProbabilityLabel: 'Scenario survival 42% · uncalibrated simulation',
      rosterImpact: malicious,
      riskLabel: 'Injury/news: unknown — not assumed healthy',
      riskSourceLabel: 'Source: FantasyPros · updated 2026-09-01T22:15:00Z',
      recentNews: [malicious],
      breakoutLabel: 'Breakout Watch · uncalibrated',
      breakoutDetail: 'Example Projections · as of 2026-08-20 · 210 projected points · 125 targets · year 2',
      breakoutMethod: malicious,
      projectionLabel: 'FantasyPros projection evidence',
      projectionDetail: '2026 PPR · 294.5 projected points · 124.25 receptions · fresh cached snapshot',
      projectionCaution: 'Projection evidence only; FantasyPros does not supply experience years, so this evidence alone does not create a Breakout Watch label.',
      reasoning: [malicious],
    }],
    contingency: ['If unavailable: Player 2'],
    emptyMessage: '',
  }, { document: fakeDocument });

  assert.equal(globalThis.pwned, false);
  assert.ok(findAll(root, (node) => node._textContent === malicious).length >= 4);
  assert.match(root.textContent, /Starter tier/);
  assert.equal(findAll(root, (node) => node.tagName === 'section').length >= 3, true);
  assert.equal(findAll(root, (node) => node.tagName === 'h2').length >= 2, true);
  assert.equal(root.attributes['aria-live'], 'polite');
  assert.equal(findAll(root, (node) => node.className === 'news-list').length, 1);
  assert.equal(findAll(root, (node) => node.className.includes('decision-brief--next')).length, 1);
  assert.match(root.textContent, /Recommended now/);
  assert.match(root.textContent, /If unavailable/);
  assert.match(root.textContent, /Next two selections/);
  assert.match(root.textContent, /uncalibrated heuristic/);
  assert.match(root.textContent, /Breakout Watch · uncalibrated/);
  assert.match(root.textContent, /FantasyPros projection evidence/);
  assert.equal(findAll(root, (node) => (
    node.attributes['aria-label'] === 'FantasyPros projection evidence'
  )).length, 1);
  assert.match(root.textContent, /Breakout evidence is unavailable/);
});

test('normalizes and renders market signals, two-pick plan, and breakout notice together', () => {
  const breakoutNotice = 'Breakout evidence is unavailable for this ranking profile.';
  const recommendation = {
    player: {
      name: 'Integrated Target',
      position: 'WR',
      team: 'SEA',
      rank: 7,
      adp: null,
      adpAvailable: false,
    },
    overallScore: 91.5,
    confidence: 0.82,
    confidenceCalibrated: false,
    returnProbability: 0.31,
    rosterImpact: 'fills an unfilled WR starter or flex requirement',
    reasoning: ['ADP is unavailable; rank fallback is used only for scoring.'],
    risk: { status: 'unknown', fresh: false },
    specialistDetails: {
      scenario: { survivalProbability: 0.42, calibrated: false },
      value: { tier: 'elite' },
    },
    decisionSignals: {
      badges: [],
      action: {
        code: 'timing-unknown',
        label: 'Timing unknown',
        reason: 'Real ADP is unavailable.',
        calibrated: false,
      },
      riskCaution: null,
    },
  };
  const response = {
    status: 'success',
    leagueId: '10462193',
    generatedAt: '2026-09-02T12:00:00Z',
    state: {
      currentOverallPick: 7,
      nextUserPick: 11,
      picksUntilUserTurn: 4,
      teamCount: 4,
      userRoster: [],
      health: {
        complete: true,
        fresh: true,
        teamCountSource: 'league',
        stateAgeSeconds: 0,
        missingPickNumbers: [],
        duplicatePickNumbers: [],
        unnumberedPickCount: 0,
      },
    },
    capabilities: { injuryStatus: false, externalNews: false, breakoutWatch: false },
    critic: { passed: true, checks: { allDraftedPlayersResolved: true } },
    recommendations: [recommendation],
    marketSignals: {
      status: 'available',
      calibrated: false,
      method: 'Uncalibrated deterministic rank-versus-ADP market heuristic.',
      message: 'The bounded market board is available.',
      scope: 'Counts cover the supplied ranking rows only.',
      source: {
        name: 'Unit test rankings',
        season: 2026,
        targetSeason: 2026,
        sameSeason: true,
        asOf: '2026-09-01',
        asOfBasis: 'source',
      },
      definitions: [{
        code: 'value',
        label: 'Value',
        description: 'Current pick is at least one league round after real ADP.',
      }],
      trust: [{
        code: 'ledger-complete',
        passed: true,
        message: 'Authoritative numbered ledger is complete.',
      }],
      exclusions: [{
        code: 'no-real-adp',
        count: 1,
        message: '1 available ranking candidate has no real ADP.',
      }],
      sleeperWatch: [],
    },
    nextTwoPicksPlan: {
      status: 'degraded',
      method: 'Bounded deterministic candidate-pair scoring.',
      probabilitiesCalibrated: false,
      primaryNow: {
        name: 'Integrated Target', position: 'WR', team: 'SEA', score: 91.5,
      },
      fallbacksNow: [],
      nextUserPicks: [11, 14],
      combinations: [],
      uncertainties: ['Actual ADP is unavailable for the primary candidate.'],
      summary: 'The immediate board is usable with limited future timing evidence.',
    },
    cockpit: {
      breakoutWatch: {
        status: 'unavailable',
        calibrated: false,
        message: breakoutNotice,
      },
    },
    warnings: [],
  };
  const model = createRecommendationViewModel(response, { leagueId: '10462193' });
  const root = new FakeElement('main');

  assert.equal(model.marketSignals.status, 'available');
  assert.equal(model.nextTwoPicksPlan.status, 'degraded');
  assert.equal(model.breakoutEvidenceNotice, breakoutNotice);
  renderRecommendationView(root, model, { document: fakeDocument });

  assert.equal(findAll(root, (node) => (
    node.className.split(' ').includes('market-signals')
  )).length, 1);
  assert.equal(findAll(root, (node) => (
    node.className.split(' ').includes('two-pick-plan')
  )).length, 1);
  assert.equal(findAll(root, (node) => (
    node.attributes['aria-label'] === 'Breakout evidence availability'
  )).length, 1);
  assert.match(root.textContent, /Sleeper Watch/);
  assert.match(root.textContent, /Next two selections/);
  assert.match(root.textContent, new RegExp(breakoutNotice));
});

test('renders exact blocker details and an empty-state instead of player cards', () => {
  const root = new FakeElement('main');
  renderRecommendationView(root, {
    mode: 'blocked',
    leagueLabel: 'League 123',
    statusTitle: 'Recommendations blocked',
    statusMessage: 'Repair the authoritative ledger.',
    actionNotice: 'Recommendations only — this assistant never drafts players.',
    draftContext: [],
    ledgerIssues: ['Missing pick numbers: 3', 'Duplicate pick numbers: 5'],
    degradations: [],
    decisionBrief: null,
    nextTwoPicksPlan: null,
    breakoutEvidenceNotice: null,
    recommendations: [],
    contingency: [],
    emptyMessage: 'Open Results → Round by Round and use Full rescan & repair.',
  }, { document: fakeDocument });

  assert.match(root.textContent, /Missing pick numbers: 3/);
  assert.match(root.textContent, /Duplicate pick numbers: 5/);
  assert.match(root.textContent, /Full rescan & repair/);
  assert.equal(findAll(root, (node) => node.className === 'recommendation-card').length, 0);
  assert.equal(findAll(root, (node) => node.className.includes('decision-brief')).length, 0);
});

test('renders an available AI critic after deterministic recommendations as inert advisory text', () => {
  globalThis.pwned = false;
  const malicious = '<img src=x onerror="globalThis.pwned=true">';
  const root = new FakeElement('main');
  renderRecommendationView(root, {
    mode: 'success',
    leagueLabel: 'League 123',
    statusTitle: 'Recommendations ready',
    statusMessage: 'Ready.',
    actionNotice: 'Recommendations only — this assistant never drafts players.',
    draftContext: [],
    ledgerIssues: [],
    degradations: [],
    nextTwoPicksPlan: null,
    breakoutEvidenceNotice: null,
    recommendations: [{
      rankLabel: '1',
      name: 'Deterministic Player',
      playerMeta: 'WR · SEA',
      valueLabel: 'Rank 1',
      scoreLabel: 'Score 88.2',
      confidenceLabel: 'Confidence 81% · uncalibrated',
      returnProbabilityLabel: 'Estimated return 37% · uncalibrated heuristic',
      scenarioProbabilityLabel: 'Scenario survival 42% · uncalibrated simulation',
      rosterImpact: 'Fills WR.',
      riskLabel: 'Injury/news: unknown — not assumed healthy',
      riskSourceLabel: '',
      recentNews: [],
      reasoning: [],
    }],
    advisoryCritic: {
      status: 'available',
      provider: 'Databricks',
      model: malicious,
      advisoryOnly: true,
      summary: malicious,
      cautions: [malicious],
      cached: true,
      latencyMs: 321,
    },
    contingency: ['Re-run after every pick.'],
    emptyMessage: '',
  }, { document: fakeDocument });

  const recommendationIndex = root.children.findIndex((node) => node.className === 'recommendations');
  const criticIndex = root.children.findIndex((node) => (
    node.className.split(' ').includes('advisory-critic')
  ));
  const contingencyIndex = root.children.findIndex((node) => node.className === 'contingency');
  assert.ok(recommendationIndex >= 0 && criticIndex > recommendationIndex);
  assert.ok(contingencyIndex > criticIndex);
  assert.match(root.textContent, /AI critic — advisory only/);
  assert.match(root.textContent, /does not change deterministic recommendation order, scores, or confidence/i);
  assert.match(root.textContent, /Cached response · 321 ms/);
  assert.ok(findAll(root, (node) => node._textContent === malicious).length >= 2);
  assert.match(root.textContent, new RegExp(malicious.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.equal(globalThis.pwned, false);
});

test('renders only the generic reason details for an unavailable AI critic and nothing when absent', () => {
  const base = {
    mode: 'success',
    leagueLabel: 'League 123',
    statusTitle: 'Recommendations ready',
    statusMessage: 'Ready.',
    actionNotice: 'Recommendations only — this assistant never drafts players.',
    draftContext: [],
    ledgerIssues: [],
    degradations: [],
    recommendations: [],
    contingency: [],
    emptyMessage: 'No recommendations.',
  };
  const unavailableRoot = new FakeElement('main');
  renderRecommendationView(unavailableRoot, {
    ...base,
    advisoryCritic: {
      status: 'unavailable',
      provider: 'Databricks',
      model: 'secret-model-detail',
      advisoryOnly: true,
      cached: false,
      latencyMs: 20,
      unavailableReason: {
        code: 'timeout',
        message: 'The optional AI critic timed out.',
      },
    },
  }, { document: fakeDocument });

  assert.match(unavailableRoot.textContent, /The optional AI critic timed out\./);
  assert.doesNotMatch(unavailableRoot.textContent, /secret-model-detail|timeoutCached|timeoutLive/);
  assert.equal(findAll(unavailableRoot, (node) => (
    node.className.split(' ').includes('advisory-critic')
  )).length, 1);

  const absentRoot = new FakeElement('main');
  renderRecommendationView(absentRoot, base, { document: fakeDocument });
  assert.equal(findAll(absentRoot, (node) => (
    node.className.split(' ').includes('advisory-critic')
  )).length, 0);
});

test('renders compact decision badges and bounded sleeper-watch evidence as inert text', () => {
  globalThis.pwned = false;
  const malicious = '<img src=x onerror="globalThis.pwned=true">';
  const root = new FakeElement('main');
  renderRecommendationView(root, {
    mode: 'success',
    leagueLabel: 'League 123',
    statusTitle: 'Recommendations ready',
    statusMessage: 'Ready.',
    actionNotice: 'Recommendations only — this assistant never drafts players.',
    draftContext: [],
    ledgerIssues: [],
    degradations: [],
    decisionBrief: {
      turnLabel: 'You are next',
      turnTone: 'next',
      primaryLabel: 'Recommended now',
      primaryName: 'Market Target',
      primaryMeta: 'WR · SEA',
      primaryAction: 'Take now',
      primaryBadges: ['Value', 'Sleeper Watch'],
      fallbacks: [],
    },
    marketSignals: {
      status: 'available',
      message: 'Five transparent late-market targets.',
      sourceLabel: 'DraftSheets 2026 · season 2026 · as of 2026-09-01',
      methodLabel: 'Uncalibrated deterministic rank-versus-ADP market heuristic.',
      scope: 'Counts cover only the bounded ranking frontier.',
      definitions: [
        'Value: at least one league round past real ADP.',
        'Sleeper Watch: Round 7+ and rank beats real ADP by at least one league round.',
        'Fade: rank trails real ADP by at least one league round.',
      ],
      trust: ['Ready: Authoritative ledger complete.'],
      exclusions: ['24 drafted players excluded.', '3 players have no real ADP.'],
      sleeperWatch: [{
        name: malicious,
        playerMeta: 'WR · SEA',
        summary: 'Ranked 24 picks ahead of real ADP.',
        badges: [{ code: 'sleeper-watch', label: 'Sleeper Watch', detail: 'Market discount' }],
        actionLabel: 'Can wait',
        actionReason: 'Uncalibrated timing estimate.',
        riskCaution: malicious,
      }],
    },
    recommendations: [{
      rankLabel: '1',
      name: 'Market Target',
      playerMeta: 'WR · SEA',
      valueLabel: 'Rank 20 · ADP 44',
      scoreLabel: 'Score 88.2',
      confidenceLabel: 'Confidence 81% · uncalibrated',
      returnProbabilityLabel: 'Estimated return 37% · uncalibrated heuristic',
      scenarioProbabilityLabel: 'Scenario survival 42% · uncalibrated simulation',
      rosterImpact: 'Fills WR.',
      riskLabel: 'Injury/news: unknown — not assumed healthy',
      riskSourceLabel: '',
      recentNews: [],
      reasoning: [],
      badges: [
        { code: 'value', label: 'Value', detail: '12 picks past real ADP' },
        { code: 'sleeper-watch', label: 'Sleeper Watch', detail: 'Ranked ahead of market' },
      ],
      actionLabel: 'Take now',
      actionReason: malicious,
      riskCaution: '',
    }],
    advisoryCritic: null,
    contingency: [],
    emptyMessage: '',
  }, { document: fakeDocument });

  assert.equal(globalThis.pwned, false);
  assert.match(root.textContent, /Sleeper Watch/);
  assert.match(root.textContent, /Take now/);
  assert.match(root.textContent, /Can wait/);
  assert.match(root.textContent, /real ADP/);
  assert.doesNotMatch(root.textContent, /Breakout/);
  assert.ok(findAll(root, (node) => node.className.includes('decision-badge')).length >= 3);
  assert.equal(findAll(root, (node) => node.className === 'sleeper-watch-item').length, 1);
  assert.ok(findAll(root, (node) => node._textContent === malicious).length >= 2);
});
