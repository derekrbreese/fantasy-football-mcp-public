const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const extensionRoot = path.join(__dirname, '..');
const stylesheets = [
  path.join(extensionRoot, 'assistant.css'),
  path.join(extensionRoot, '..', 'src', 'dashboard', 'styles.css'),
];

function rule(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 's'));
  return match?.[1] || '';
}

test('risk source and recent news use compact explicit typography', () => {
  for (const filename of stylesheets) {
    const css = fs.readFileSync(filename, 'utf8');
    assert.match(rule(css, '.risk-source'), /font-size\s*:/, filename);
    assert.match(rule(css, '.risk-source'), /overflow-wrap\s*:/, filename);
    assert.match(rule(css, '.recent-news'), /margin-top\s*:/, filename);
    assert.match(rule(css, '.recent-news h4'), /font-size\s*:/, filename);
    assert.match(rule(css, '.news-list'), /font-size\s*:/, filename);
    assert.match(rule(css, '.news-list'), /line-height\s*:/, filename);
    assert.match(rule(css, '.news-list'), /overflow-wrap\s*:\s*anywhere/, filename);
  }
});
