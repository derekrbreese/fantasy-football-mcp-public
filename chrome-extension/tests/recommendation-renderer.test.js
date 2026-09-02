const test = require('node:test');
const assert = require('node:assert/strict');

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
    recommendations: [],
    contingency: [],
    emptyMessage: 'Open Results → Round by Round and use Full rescan & repair.',
  }, { document: fakeDocument });

  assert.match(root.textContent, /Missing pick numbers: 3/);
  assert.match(root.textContent, /Duplicate pick numbers: 5/);
  assert.match(root.textContent, /Full rescan & repair/);
  assert.equal(findAll(root, (node) => node.className === 'recommendation-card').length, 0);
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
