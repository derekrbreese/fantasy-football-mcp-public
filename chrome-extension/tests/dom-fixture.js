const fs = require('node:fs');
const path = require('node:path');

function text(value) {
  return value
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<\/t[dh]>/gi, ' ')
    .replace(/<[^>]+>/g, '')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

function rowElement(rowHtml) {
  const cells = [...rowHtml.matchAll(/<td(?:\s[^>]*)?>([\s\S]*?)<\/td>/gi)]
    .map(([, value]) => text(value));
  const roleCells = [...rowHtml.matchAll(/<[^>]+role=["']cell["'][^>]*>([\s\S]*?)<\/[^>]+>/gi)]
    .map(([, value]) => text(value));
  return {
    innerText: text(rowHtml),
    querySelectorAll(selector) {
      return selector === 'td'
        ? cells.map((textContent) => ({ textContent, innerText: textContent }))
        : selector === '[role="cell"]'
          ? roleCells.map((textContent) => ({ textContent, innerText: textContent }))
          : [];
    },
  };
}

function tableElement(tableHtml) {
  const heading = tableHtml.match(/<thead(?:\s[^>]*)?>([\s\S]*?)<\/thead>/i)?.[1] || '';
  const body = tableHtml.match(/<tbody(?:\s[^>]*)?>([\s\S]*?)<\/tbody>/i)?.[1] || '';
  const rows = [...body.matchAll(/<tr(?:\s[^>]*)?>([\s\S]*?)<\/tr>/gi)]
    .map(([, rowHtml]) => rowElement(rowHtml));
  return {
    querySelector(selector) {
      return selector === 'thead' ? { innerText: text(heading) } : null;
    },
    querySelectorAll(selector) {
      return selector === 'tr' ? rows : [];
    },
  };
}

function loadDomFixture(filename) {
  const html = fs.readFileSync(path.join(__dirname, 'fixtures', filename), 'utf8');
  const tables = [...html.matchAll(/<table(?:\s[^>]*)?>([\s\S]*?)<\/table>/gi)]
    .map(([, tableHtml]) => tableElement(tableHtml));
  return {
    querySelectorAll(selector) {
      return selector === 'table' ? tables : [];
    },
  };
}

module.exports = { loadDomFixture };
