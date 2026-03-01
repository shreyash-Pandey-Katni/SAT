/**
 * capture.js  —  injected via page.addInitScript() into EVERY frame/page.
 *
 * Communicates with Python via page.exposeFunction():
 *   window.__sat_click(eventData)
 *   window.__sat_input(eventData)
 *   window.__sat_select(eventData)
 *
 * All functions are exposed by the EventListener before this script runs.
 */
(function () {
  'use strict';

  // ── Helpers ──────────────────────────────────────────────────────────────

  function getAttr(el, name) {
    return el.getAttribute(name) || null;
  }

  function truncate(s, n) {
    if (!s) return null;
    s = s.trim();
    return s.length > n ? s.substring(0, n) : s;
  }

  /**
   * Walk up the DOM to build a unique CSS selector for *el*.
   * Prefers id, then data-testid, then positional nth-child path.
   */
  function computeUniqueSelector(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    if (el.id) return '#' + CSS.escape(el.id);

    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'BODY') {
      let selector = node.tagName.toLowerCase();
      if (node.id) {
        selector = '#' + CSS.escape(node.id);
        parts.unshift(selector);
        break;
      }
      const testId =
        node.getAttribute('data-testid') ||
        node.getAttribute('data-test-id') ||
        node.getAttribute('data-cy');
      if (testId) {
        selector += '[data-testid="' + testId + '"]';
        parts.unshift(selector);
        break;
      }
      // nth-child to ensure uniqueness
      let nth = 1;
      let sib = node.previousSibling;
      while (sib) {
        if (sib.nodeType === Node.ELEMENT_NODE && sib.tagName === node.tagName) nth++;
        sib = sib.previousSibling;
      }
      selector += ':nth-of-type(' + nth + ')';
      parts.unshift(selector);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  /**
   * Build an absolute XPath for *el*.
   */
  function computeXPath(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE) {
      let idx = 1;
      let sib = node.previousSibling;
      while (sib) {
        if (sib.nodeType === Node.ELEMENT_NODE && sib.nodeName === node.nodeName) idx++;
        sib = sib.previousSibling;
      }
      parts.unshift(node.nodeName.toLowerCase() + '[' + idx + ']');
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  }

  /**
   * Collect all relevant attributes from an element.
   */
  function extractElementData(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const rect = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      className: truncate(el.className, 200),
      name: getAttr(el, 'name'),
      text: truncate(el.textContent, 200),
      ariaLabel: getAttr(el, 'aria-label'),
      placeholder: getAttr(el, 'placeholder'),
      dataTestId:
        getAttr(el, 'data-testid') ||
        getAttr(el, 'data-test-id') ||
        getAttr(el, 'data-cy'),
      href: getAttr(el, 'href'),
      role: getAttr(el, 'role'),
      inputType: el.tagName === 'INPUT' ? (getAttr(el, 'type') || 'text') : null,
      outerHTML: truncate(el.outerHTML, 500),
      parentHTML: el.parentElement
        ? truncate(el.parentElement.outerHTML, 300)
        : null,
      css: computeUniqueSelector(el),
      xpath: computeXPath(el),
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        scrollX: window.scrollX,
        scrollY: window.scrollY,
      },
    };
  }

  // ── Click capture ────────────────────────────────────────────────────────
  document.addEventListener(
    'click',
    function (e) {
      var el = e.target;
      var data = extractElementData(el);
      if (!data) return;
      data.clientX = e.clientX;
      data.clientY = e.clientY;
      if (window.__sat_click) window.__sat_click(data);
    },
    true // capture phase — fires before any page handler
  );

  // ── Input / type capture (debounced) ─────────────────────────────────────
  var inputTimer = null;
  document.addEventListener(
    'input',
    function (e) {
      var el = e.target;
      clearTimeout(inputTimer);
      inputTimer = setTimeout(function () {
        var data = extractElementData(el);
        if (!data) return;
        data.value = el.value !== undefined ? el.value : null;
        if (window.__sat_input) window.__sat_input(data);
      }, 500);
    },
    true
  );

  // ── Select capture ───────────────────────────────────────────────────────
  document.addEventListener(
    'change',
    function (e) {
      var el = e.target;
      if (el.tagName !== 'SELECT') return;
      var data = extractElementData(el);
      if (!data) return;
      data.value = el.value;
      data.selectedText =
        el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : null;
      if (window.__sat_select) window.__sat_select(data);
    },
    true
  );
})();
