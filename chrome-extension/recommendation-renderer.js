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

  function renderBadges(documentRef, badges) {
    const row = element(documentRef, 'div', 'decision-badges');
    for (const badge of Array.isArray(badges) ? badges.slice(0, 3) : []) {
      const code = typeof badge === 'string' ? '' : badge?.code;
      const label = typeof badge === 'string' ? badge : badge?.label;
      const detail = typeof badge === 'string' ? '' : badge?.detail;
      const node = element(
        documentRef,
        'span',
        `decision-badge${code ? ` decision-badge--${code}` : ''}`,
        label,
      );
      if (detail) node.setAttribute('title', detail);
      row.appendChild(node);
    }
    return row;
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

  function renderDecisionBrief(documentRef, brief) {
    const tone = ['urgent', 'next', 'watch', 'unknown'].includes(brief?.turnTone)
      ? brief.turnTone
      : 'unknown';
    const section = element(documentRef, 'section', `decision-brief decision-brief--${tone}`);
    section.setAttribute('aria-label', 'At-a-glance draft decision');
    section.appendChild(element(documentRef, 'p', 'turn-status', brief.turnLabel));

    const primary = element(documentRef, 'div', 'decision-primary');
    primary.appendChild(element(documentRef, 'span', 'decision-label', brief.primaryLabel));
    primary.appendChild(element(documentRef, 'strong', 'decision-name', brief.primaryName));
    primary.appendChild(element(documentRef, 'span', 'decision-meta', brief.primaryMeta));
    if (brief.primaryBadges?.length) {
      primary.appendChild(renderBadges(documentRef, brief.primaryBadges));
    }
    if (brief.primaryAction) {
      primary.appendChild(element(
        documentRef,
        'span',
        'decision-action decision-action--primary',
        brief.primaryAction,
      ));
    }
    section.appendChild(primary);

    if (brief.fallbacks?.length) {
      const fallback = element(documentRef, 'div', 'decision-fallbacks');
      fallback.appendChild(element(documentRef, 'span', 'decision-label', 'If unavailable'));
      const fallbackList = element(documentRef, 'div', 'fallback-list');
      for (const candidate of brief.fallbacks) {
        const item = element(documentRef, 'span', 'fallback-item');
        item.appendChild(element(documentRef, 'strong', '', candidate.name));
        item.appendChild(element(documentRef, 'small', '', candidate.meta));
        fallbackList.appendChild(item);
      }
      fallback.appendChild(fallbackList);
      section.appendChild(fallback);
    }
    return section;
  }

  function renderNextTwoPicksPlan(documentRef, plan) {
    const status = plan?.status === 'ready' ? 'ready' : 'degraded';
    const section = element(documentRef, 'section', `two-pick-plan two-pick-plan--${status}`);
    section.setAttribute('aria-label', 'Next two selections plan');
    section.appendChild(element(documentRef, 'h2', '', 'Next two selections'));
    section.appendChild(element(documentRef, 'p', 'two-pick-status', plan.statusLabel));
    section.appendChild(element(documentRef, 'p', 'two-pick-summary', plan.summary));
    section.appendChild(element(documentRef, 'p', 'two-pick-order', plan.pickLabel));
    section.appendChild(element(documentRef, 'p', 'two-pick-primary', plan.primaryLabel));
    if (plan.fallbackLabels?.length) {
      section.appendChild(list(documentRef, plan.fallbackLabels, 'two-pick-fallbacks'));
    }
    if (plan.combinations?.length) {
      const combinations = element(documentRef, 'section', 'two-pick-combinations');
      combinations.appendChild(element(documentRef, 'h3', '', 'Position-aware combinations'));
      for (const combination of plan.combinations) {
        const combinationNode = element(documentRef, 'article', 'two-pick-combination');
        combinationNode.appendChild(element(documentRef, 'strong', '', combination.label));
        combinationNode.appendChild(element(
          documentRef,
          'p',
          'two-pick-availability',
          combination.availabilityLabel,
        ));
        if (combination.reasons?.length) {
          combinationNode.appendChild(list(documentRef, combination.reasons, 'two-pick-reasons'));
        }
        combinations.appendChild(combinationNode);
      }
      section.appendChild(combinations);
    }
    if (plan.uncertainties?.length) {
      const uncertainties = element(documentRef, 'section', 'two-pick-uncertainties');
      uncertainties.appendChild(element(documentRef, 'h3', '', 'Planning uncertainties'));
      uncertainties.appendChild(list(documentRef, plan.uncertainties, 'two-pick-uncertainty-list'));
      section.appendChild(uncertainties);
    }
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

    if (recommendation.badges?.length) {
      card.appendChild(renderBadges(documentRef, recommendation.badges));
    }

    if (recommendation.actionLabel) {
      const action = element(documentRef, 'p', 'decision-action');
      action.appendChild(element(documentRef, 'strong', '', recommendation.actionLabel));
      if (recommendation.actionReason) {
        action.appendChild(element(documentRef, 'span', '', ` · ${recommendation.actionReason}`));
      }
      card.appendChild(action);
    }

    if (recommendation.valueLabel) {
      card.appendChild(element(documentRef, 'p', 'value-label', recommendation.valueLabel));
    }
    if (recommendation.projectionLabel) {
      const projection = element(documentRef, 'section', 'projection-evidence');
      projection.setAttribute('aria-label', 'FantasyPros projection evidence');
      projection.appendChild(element(
        documentRef,
        'strong',
        'projection-label',
        recommendation.projectionLabel,
      ));
      projection.appendChild(element(
        documentRef,
        'p',
        'projection-detail',
        recommendation.projectionDetail,
      ));
      projection.appendChild(element(
        documentRef,
        'p',
        'projection-caution',
        recommendation.projectionCaution,
      ));
      card.appendChild(projection);
    }
    if (recommendation.breakoutLabel) {
      const breakout = element(documentRef, 'section', 'breakout-watch');
      breakout.setAttribute('aria-label', 'Breakout Watch evidence');
      breakout.appendChild(element(documentRef, 'strong', 'breakout-label', recommendation.breakoutLabel));
      breakout.appendChild(element(documentRef, 'p', 'breakout-detail', recommendation.breakoutDetail));
      breakout.appendChild(element(documentRef, 'p', 'breakout-method', recommendation.breakoutMethod));
      card.appendChild(breakout);
    }

    const metrics = element(documentRef, 'div', 'recommendation-metrics');
    metrics.appendChild(element(documentRef, 'span', 'metric', recommendation.confidenceLabel));
    metrics.appendChild(element(documentRef, 'span', 'metric', recommendation.returnProbabilityLabel));
    metrics.appendChild(element(documentRef, 'span', 'metric', recommendation.scenarioProbabilityLabel));
    card.appendChild(metrics);
    card.appendChild(element(documentRef, 'p', 'roster-impact', recommendation.rosterImpact));
    card.appendChild(element(documentRef, 'p', 'risk-label', recommendation.riskLabel));
    if (recommendation.riskCaution) {
      card.appendChild(element(documentRef, 'p', 'market-risk-caution', recommendation.riskCaution));
    }
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

  function renderMarketSignals(documentRef, market) {
    if (!market || !['available', 'blocked', 'unavailable'].includes(market.status)) return null;
    const section = element(
      documentRef,
      'section',
      `market-signals market-signals--${market.status}`,
    );
    section.setAttribute('aria-label', 'Sleeper Watch market signals');
    section.appendChild(element(documentRef, 'h2', '', 'Sleeper Watch'));
    section.appendChild(element(documentRef, 'p', 'market-message', market.message));
    if (market.sourceLabel) {
      section.appendChild(element(documentRef, 'p', 'market-source', market.sourceLabel));
    }
    if (market.methodLabel) {
      section.appendChild(element(documentRef, 'p', 'market-method', market.methodLabel));
    }
    if (market.scope) section.appendChild(element(documentRef, 'p', 'market-scope', market.scope));

    if (market.sleeperWatch?.length) {
      const watchList = element(documentRef, 'div', 'sleeper-watch-list');
      for (const sleeper of market.sleeperWatch.slice(0, 5)) {
        const item = element(documentRef, 'article', 'sleeper-watch-item');
        const identity = element(documentRef, 'div', 'sleeper-watch-identity');
        identity.appendChild(element(documentRef, 'strong', '', sleeper.name));
        identity.appendChild(element(documentRef, 'span', '', sleeper.playerMeta));
        item.appendChild(identity);
        if (sleeper.badges?.length) item.appendChild(renderBadges(documentRef, sleeper.badges));
        item.appendChild(element(documentRef, 'p', 'market-summary', sleeper.summary));
        if (sleeper.actionLabel) {
          item.appendChild(element(
            documentRef,
            'p',
            'decision-action',
            `${sleeper.actionLabel}${sleeper.actionReason ? ` · ${sleeper.actionReason}` : ''}`,
          ));
        }
        if (sleeper.riskCaution) {
          item.appendChild(element(documentRef, 'p', 'market-risk-caution', sleeper.riskCaution));
        }
        watchList.appendChild(item);
      }
      section.appendChild(watchList);
    }

    const explanations = [
      ['Definitions', market.definitions],
      ['Trust checks', market.trust],
      ['Bounded exclusions', market.exclusions],
    ];
    for (const [label, values] of explanations) {
      if (!values?.length) continue;
      const details = element(documentRef, 'details', 'market-details');
      details.appendChild(element(documentRef, 'summary', '', label));
      details.appendChild(list(documentRef, values, 'market-detail-list'));
      section.appendChild(details);
    }
    return section;
  }

  function renderAdvisoryCritic(documentRef, critic) {
    if (
      !critic ||
      critic.advisoryOnly !== true ||
      !['available', 'unavailable'].includes(critic.status)
    ) return null;

    const section = element(
      documentRef,
      'section',
      `notice notice--quality advisory-critic advisory-critic--${critic.status}`,
    );
    section.setAttribute('aria-label', 'AI critic advisory');
    section.appendChild(element(documentRef, 'h2', '', 'AI critic — advisory only'));
    section.appendChild(element(
      documentRef,
      'p',
      'advisory-critic-disclaimer',
      'Optional AI commentary does not change deterministic recommendation order, scores, or confidence.',
    ));

    if (critic.status === 'unavailable') {
      section.appendChild(element(
        documentRef,
        'p',
        'advisory-critic-message',
        critic.unavailableReason?.message || 'The optional AI critic is unavailable.',
      ));
      return section;
    }

    section.appendChild(element(
      documentRef,
      'p',
      'advisory-critic-source',
      `${critic.provider} · ${critic.model}`,
    ));
    section.appendChild(element(documentRef, 'p', 'advisory-critic-summary', critic.summary));
    if (critic.cautions?.length) {
      const cautions = element(documentRef, 'section', 'advisory-critic-cautions');
      cautions.appendChild(element(documentRef, 'h3', '', 'AI cautions'));
      cautions.appendChild(list(documentRef, critic.cautions, 'advisory-critic-caution-list'));
      section.appendChild(cautions);
    }
    const latency = typeof critic.latencyMs === 'number'
      ? `${critic.latencyMs} ms`
      : 'latency unavailable';
    section.appendChild(element(
      documentRef,
      'p',
      'advisory-critic-meta',
      `${critic.cached ? 'Cached' : 'Live'} response · ${latency}`,
    ));
    return section;
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

    if (model.decisionBrief) {
      root.appendChild(renderDecisionBrief(documentRef, model.decisionBrief));
    }

    const marketSignals = renderMarketSignals(documentRef, model.marketSignals);
    if (marketSignals) root.appendChild(marketSignals);

    if (model.nextTwoPicksPlan) {
      root.appendChild(renderNextTwoPicksPlan(documentRef, model.nextTwoPicksPlan));
    }

    if (model.breakoutEvidenceNotice) {
      const breakoutNotice = element(documentRef, 'section', 'notice notice--quality');
      breakoutNotice.setAttribute('aria-label', 'Breakout evidence availability');
      breakoutNotice.appendChild(element(documentRef, 'h2', '', 'Breakout evidence unavailable'));
      breakoutNotice.appendChild(element(documentRef, 'p', '', model.breakoutEvidenceNotice));
      root.appendChild(breakoutNotice);
    }

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

    const advisoryCritic = renderAdvisoryCritic(documentRef, model.advisoryCritic);
    if (advisoryCritic) root.appendChild(advisoryCritic);

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
