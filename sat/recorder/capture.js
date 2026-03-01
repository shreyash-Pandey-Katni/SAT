/**
 * capture.js - injected into EVERY frame/page via two paths:
 *   1. page.addInitScript()  - runs on every future navigation / child-frame attach
 *   2. frame.evaluate()      - runs immediately on already-loaded frames
 *
 * Communicates with Python via page.exposeFunction() (CDP binding):
 *   window.__sat_click(eventData)
 *   window.__sat_input(eventData)
 *   window.__sat_select(eventData)
 *
 * Shadow DOM strategy:
 *   Listeners are installed ONLY on document in the capture phase.
 *   For open shadow roots (the vast majority), click/input/change events
 *   are "composed" - they bubble through shadow boundaries to the document.
 *   e.composedPath()[0] gives the real innermost target element even when
 *   the standard e.target is retargeted to the shadow host.
 *   This avoids installing duplicate listeners on each ShadowRoot in the
 *   hierarchy, which previously caused Nx firing for N-deep shadow nesting.
 *
 * iframe strategy:
 *   This script runs inside every iframe (Playwright's addInitScript covers
 *   all frames).  frameUrl / isIframe are included in every payload so the
 *   executor can scope locators to the correct frame.
 */
(function () {
  'use strict';

  /* --- Guard: prevent double-installation in the same JS context --- */
  if (window.__sat_capture_installed) return;
  window.__sat_capture_installed = true;

  /* ==================================================================
   * Dedup - prevent the same physical user interaction from being
   * reported more than once (safety net for edge cases).
   * ================================================================== */
  var _lastEvents = {};
  var DEDUP_MS = 60;

  function isDuplicate(eventType, el) {
    var now = Date.now();
    var prev = _lastEvents[eventType];
    if (prev && prev.el === el && (now - prev.ts) < DEDUP_MS) return true;
    _lastEvents[eventType] = { el: el, ts: now };
    return false;
  }

  /* ==================================================================
   * Helpers
   * ================================================================== */

  function getAttr(el, name) {
    try { return el.getAttribute(name) || null; } catch (_) { return null; }
  }

  function truncate(s, n) {
    if (!s) return null;
    if (typeof s !== 'string') s = String(s);
    s = s.trim();
    return s.length > n ? s.substring(0, n) : s;
  }

  /** Is el inside an open shadow root? */
  function isInShadowDom(el) {
    try {
      var root = el.getRootNode();
      return root instanceof ShadowRoot;
    } catch (_) { return false; }
  }

  /**
   * CSS selector that crosses shadow boundaries via the host element.
   * Playwright's CSS engine pierces open shadow roots automatically.
   */
  function computeUniqueSelector(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    if (el.id) return '#' + CSS.escape(el.id);

    var parts = [];
    var node = el;
    var maxDepth = 30;
    while (node && node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'BODY' && maxDepth-- > 0) {
      var selector = node.tagName.toLowerCase();

      if (node.id) {
        parts.unshift('#' + CSS.escape(node.id));
        break;
      }

      var testId = getAttr(node, 'data-testid') || getAttr(node, 'data-test-id') || getAttr(node, 'data-cy');
      if (testId) {
        parts.unshift(selector + '[data-testid="' + CSS.escape(testId) + '"]');
        break;
      }

      var nth = 1;
      var sib = node.previousElementSibling;
      while (sib) {
        if (sib.tagName === node.tagName) nth++;
        sib = sib.previousElementSibling;
      }
      selector += ':nth-of-type(' + nth + ')';
      parts.unshift(selector);

      // Cross shadow-DOM boundary when parentElement is null
      var parent = node.parentElement;
      if (!parent) {
        var pn = node.parentNode;
        if (pn && pn.nodeType === 11 /* ShadowRoot */) parent = pn.host;
      }
      node = parent;
    }
    return parts.join(' > ') || null;
  }

  /** XPath - null for shadow-DOM elements (XPath cannot pierce roots). */
  function computeXPath(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    if (isInShadowDom(el)) return null;

    var parts = [];
    var node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE) {
      var idx = 1;
      var sib = node.previousElementSibling;
      while (sib) {
        if (sib.nodeName === node.nodeName) idx++;
        sib = sib.previousElementSibling;
      }
      parts.unshift(node.nodeName.toLowerCase() + '[' + idx + ']');
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  }

  /** Collect attributes from an element. */
  function extractElementData(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    var rect;
    try {
      rect = el.getBoundingClientRect();
    } catch (_) {
      rect = { x: 0, y: 0, width: 0, height: 0 };
    }

    var parentEl = el.parentElement;
    if (!parentEl && el.parentNode && el.parentNode.nodeType === 11) {
      parentEl = el.parentNode.host;
    }

    return {
      tag:           el.tagName.toLowerCase(),
      id:            el.id || null,
      className:     truncate(typeof el.className === 'string' ? el.className : '', 200),
      name:          getAttr(el, 'name'),
      text:          truncate(el.textContent, 200),
      ariaLabel:     getAttr(el, 'aria-label'),
      placeholder:   getAttr(el, 'placeholder'),
      dataTestId:    getAttr(el, 'data-testid') || getAttr(el, 'data-test-id') || getAttr(el, 'data-cy'),
      href:          getAttr(el, 'href'),
      role:          getAttr(el, 'role'),
      inputType:     el.tagName === 'INPUT' ? (getAttr(el, 'type') || 'text') : null,
      outerHTML:     truncate(el.outerHTML, 500),
      parentHTML:    parentEl ? truncate(parentEl.outerHTML, 300) : null,
      css:           computeUniqueSelector(el),
      xpath:         computeXPath(el),
      inShadowDom:   isInShadowDom(el),
      frameUrl:      (window !== window.top) ? window.location.href : null,
      isIframe:      window !== window.top,
      rect:          { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      viewport: {
        width:   window.innerWidth,
        height:  window.innerHeight,
        scrollX: window.scrollX,
        scrollY: window.scrollY,
      },
    };
  }

  /** Deepest target, even inside open shadow roots. */
  function realTarget(e) {
    try {
      var path = e.composedPath && e.composedPath();
      return (path && path.length > 0) ? path[0] : e.target;
    } catch (_) { return e.target; }
  }

  /* ==================================================================
   * Event listeners - DOCUMENT ONLY (capture phase)
   *
   * For composed events (click, input, change) the document-level
   * listener receives them even when they originate inside shadow roots.
   * composedPath()[0] gives the real innermost target.
   * We do NOT install listeners on individual ShadowRoot objects because
   * that causes Nx duplication for N-deep nesting.
   * ================================================================== */

  // -- Click -----------------------------------------------------------
  document.addEventListener('click', function (e) {
    var el = realTarget(e);
    if (isDuplicate('click', el)) return;
    var data = extractElementData(el);
    if (!data) return;
    data.clientX = e.clientX;
    data.clientY = e.clientY;
    if (window.__sat_click) window.__sat_click(data);
  }, true);

  // -- Input / type (debounced 500 ms) ---------------------------------
  var inputTimer = null;
  document.addEventListener('input', function (e) {
    var el = realTarget(e);
    clearTimeout(inputTimer);
    inputTimer = setTimeout(function () {
      if (isDuplicate('input', el)) return;
      var data = extractElementData(el);
      if (!data) return;
      data.value = (el.value !== undefined) ? el.value : null;
      if (window.__sat_input) window.__sat_input(data);
    }, 500);
  }, true);

  // -- Select ----------------------------------------------------------
  document.addEventListener('change', function (e) {
    var el = realTarget(e);
    if (!el || el.tagName !== 'SELECT') return;
    if (isDuplicate('change', el)) return;
    var data = extractElementData(el);
    if (!data) return;
    data.value = el.value;
    data.selectedText = (el.options && el.options[el.selectedIndex])
      ? el.options[el.selectedIndex].text : null;
    if (window.__sat_select) window.__sat_select(data);
  }, true);

})();
