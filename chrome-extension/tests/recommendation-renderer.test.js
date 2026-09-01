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
