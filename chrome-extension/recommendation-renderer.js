(function initRecommendationRenderer(globalScope) {
  'use strict';

  function element(documentRef, tagName, className, text) {
    const node = documentRef.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function list(documentRef, values, className) {
    const node = element(documentRef, 'ul', className);
    for (const value of values) node.appendChild(element(documentRef, 'li', '', value));
    return node;
  }

  function renderDraftContext(documentRef, context) {
    const section = element(documentRef, 'section', 'draft-context');
    section.setAttribute('aria-label', 'Draft context');
    const details = element(documentRef, 'dl', 'draft-context-grid');
    for (const item of context) {
      const group = element(documentRef, 'div', 'context-item');
      group.appendChild(element(documentRef, 'dt', '', item.label));
      group.appendChild(element(documentRef, 'dd', '', item.value));
      details.appendChild(group);
    }
    section.appendChild(details);
    return section;
  }

  function renderRecommendation(documentRef, recommendation) {
    const card = element(documentRef, 'article', 'recommendation-card');
    const heading = element(documentRef, 'div', 'recommendation-heading');
    heading.appendChild(element(documentRef, 'span', 'recommendation-rank', recommendation.rankLabel));
    const identity = element(documentRef, 'div', 'recommendation-identity');
    identity.appendChild(element(documentRef, 'h3', '', recommendation.name));
    identity.appendChild(element(documentRef, 'p', 'player-meta', recommendation.playerMeta));
    heading.appendChild(identity);
    heading.appendChild(element(documentRef, 'strong', 'score', recommendation.scoreLabel));
    card.appendChild(heading);

    if (recommendation.valueLabel) {
      card.appendChild(element(documentRef, 'p', 'value-label', recommendation.valueLabel));
    }

    const metrics = element(documentRef, 'div', 'recommendation-metrics');
    metrics.appendChild(element(documentRef, 'span', 'metric', recommendation.confidenceLabel));
    metrics.appendChild(element(documentRef, 'span', 'metric', recommendation.returnProbabilityLabel));
    metrics.appendChild(element(documentRef, 'span', 'metric', recommendation.scenarioProbabilityLabel));
    card.appendChild(metrics);
    card.appendChild(element(documentRef, 'p', 'roster-impact', recommendation.rosterImpact));
    card.appendChild(element(documentRef, 'p', 'risk-label', recommendation.riskLabel));
    if (recommendation.riskSourceLabel) {
      card.appendChild(element(documentRef, 'p', 'risk-source', recommendation.riskSourceLabel));
    }
    if (recommendation.recentNews?.length) {
      const news = element(documentRef, 'section', 'recent-news');
      news.appendChild(element(documentRef, 'h4', '', 'Recent FantasyPros news'));
      news.appendChild(list(documentRef, recommendation.recentNews, 'news-list'));
      card.appendChild(news);
    }
    if (recommendation.reasoning.length) {
      card.appendChild(list(documentRef, recommendation.reasoning, 'reasoning-list'));
    }
    return card;
  }

  function renderRecommendationView(root, model, options = {}) {
    const documentRef = options.document || globalScope.document;
    if (!documentRef || !root) throw new Error('A document and root element are required.');
    root.replaceChildren();
    root.setAttribute('aria-live', 'polite');

    const status = element(documentRef, 'section', `assistant-status assistant-status--${model.mode}`);
    status.appendChild(element(documentRef, 'p', 'league-label', model.leagueLabel));
    status.appendChild(element(documentRef, 'h2', '', model.statusTitle));
    status.appendChild(element(documentRef, 'p', '', model.statusMessage));
    root.appendChild(status);

    if (model.draftContext.length) root.appendChild(renderDraftContext(documentRef, model.draftContext));

    if (model.ledgerIssues.length) {
      const blockers = element(documentRef, 'section', 'notice notice--blocked');
      blockers.appendChild(element(documentRef, 'h2', '', 'Ledger must be repaired'));
      blockers.appendChild(list(documentRef, model.ledgerIssues, 'issue-list'));
      root.appendChild(blockers);
    }

    if (model.degradations.length) {
      const quality = element(documentRef, 'section', 'notice notice--quality');
      quality.appendChild(element(documentRef, 'h2', '', 'Data quality'));
      quality.appendChild(list(documentRef, model.degradations, 'quality-list'));
      root.appendChild(quality);
    }

    const recommendationSection = element(documentRef, 'section', 'recommendations');
    recommendationSection.appendChild(element(documentRef, 'h2', '', 'Top recommendations'));
    if (model.recommendations.length) {
      for (const recommendation of model.recommendations) {
        recommendationSection.appendChild(renderRecommendation(documentRef, recommendation));
      }
    } else {
      recommendationSection.appendChild(element(documentRef, 'p', 'empty-message', model.emptyMessage));
    }
    root.appendChild(recommendationSection);

    if (model.contingency.length) {
      const contingency = element(documentRef, 'section', 'contingency');
      contingency.appendChild(element(documentRef, 'h2', '', 'Contingency'));
      contingency.appendChild(list(documentRef, model.contingency, 'contingency-list'));
      root.appendChild(contingency);
    }

    root.appendChild(element(documentRef, 'p', 'no-autodraft', model.actionNotice));
  }

  const api = { renderRecommendationView };
  globalScope.YahooDraftRecommendationRenderer = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
