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

function parseAttributes(source) {
  const attributes = {};
  for (const match of source.matchAll(/([:\w-]+)(?:=["']([^"']*)["'])?/g)) {
    const [, name, value] = match;
    attributes[name.toLowerCase()] = value ?? '';
  }
  return attributes;
}

function semanticElement(tagName, attributeSource, bodyHtml, parent = null) {
  const attributes = parseAttributes(attributeSource);
  const element = {
    tagName: tagName.toUpperCase(),
    id: attributes.id || '',
    hidden: Object.hasOwn(attributes, 'hidden'),
    innerText: text(bodyHtml),
    textContent: text(bodyHtml),
    parentElement: parent,
    getAttribute(name) {
      return attributes[String(name).toLowerCase()] ?? null;
    },
    closest(selector) {
      let current = this;
      while (current) {
        if (selector === '[role="tablist"]' && current.getAttribute?.('role') === 'tablist') return current;
        if (selector === '[role="tabpanel"]' && current.getAttribute?.('role') === 'tabpanel') return current;
        if (
          selector === '[hidden], [aria-hidden="true"]' &&
          (current.hidden || current.getAttribute?.('aria-hidden') === 'true')
        ) return current;
        current = current.parentElement;
      }
      return null;
    },
  };
  return element;
}

function picksPanelDom(html) {
  const byId = new Map();
  const tablists = [];
  const tabs = [];
  const panels = [];

  for (const match of html.matchAll(/<div([^>]*\brole=["']tablist["'][^>]*)>([\s\S]*?)<\/div>/gi)) {
    const tablist = semanticElement('div', match[1], match[2]);
    tablist.querySelectorAll = (selector) => selector === '[role="tab"]'
      ? tabs.filter((tab) => tab.parentElement === tablist)
      : [];
    tablists.push(tablist);
    if (tablist.id) byId.set(tablist.id, tablist);
    for (const tabMatch of match[2].matchAll(/<button([^>]*\brole=["']tab["'][^>]*)>([\s\S]*?)<\/button>/gi)) {
      const tab = semanticElement('button', tabMatch[1], tabMatch[2], tablist);
      tabs.push(tab);
      if (tab.id) byId.set(tab.id, tab);
    }
  }

  for (const match of html.matchAll(/<section([^>]*\brole=["']tabpanel["'][^>]*)>([\s\S]*?)<\/section>/gi)) {
    const panel = semanticElement('section', match[1], match[2]);
    const descendants = [];
    for (const itemMatch of match[2].matchAll(/<article([^>]*)>([\s\S]*?)<\/article>/gi)) {
      descendants.push(semanticElement('article', itemMatch[1], itemMatch[2], panel));
    }
    for (const statusMatch of match[2].matchAll(/<div([^>]*\brole=["']status["'][^>]*)>([\s\S]*?)<\/div>/gi)) {
      descendants.push(semanticElement('div', statusMatch[1], statusMatch[2], panel));
    }
    panel.querySelectorAll = () => descendants;
    panel.contains = (element) => descendants.includes(element);
    panels.push(panel);
    if (panel.id) byId.set(panel.id, panel);
  }

  const root = {
    getElementById(id) {
      return byId.get(id) || null;
    },
    querySelectorAll(selector) {
      if (selector === '[role="tab"]') return tabs;
      if (selector === 'table') return [];
      return [];
    },
  };
  const ownerDocument = { ...root };
  for (const element of [...tablists, ...tabs, ...panels]) element.ownerDocument = ownerDocument;
  for (const panel of panels) {
    for (const element of panel.querySelectorAll('*')) element.ownerDocument = ownerDocument;
  }
  return root;
}

function loadDomFixture(filename) {
  const html = fs.readFileSync(path.join(__dirname, 'fixtures', filename), 'utf8');
  if (/\brole=["']tabpanel["']/i.test(html)) return picksPanelDom(html);
  const tables = [...html.matchAll(/<table(?:\s[^>]*)?>([\s\S]*?)<\/table>/gi)]
    .map(([, tableHtml]) => tableElement(tableHtml));
  return {
    querySelectorAll(selector) {
      return selector === 'table' ? tables : [];
    },
  };
}

module.exports = { loadDomFixture };
