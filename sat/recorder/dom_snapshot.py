"""DOMSnapshot — captures interactable elements from the current page."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.async_api import Page

from sat.constants import INTERACTABLE_SELECTORS, OUTER_HTML_MAX_LEN, PARENT_HTML_MAX_LEN

# JavaScript that extracts interactable element data and returns a JSON array.
_EXTRACT_JS = """
() => {
    const SELECTORS = arguments[0];
    const MAX_HTML = arguments[1];
    const MAX_PARENT = arguments[2];

    function truncate(s, n) {
        if (!s) return null;
        s = s.trim();
        return s.length > n ? s.substring(0, n) : s;
    }

    function isVisible(el) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    const seen = new Set();
    const results = [];
    let idx = 0;
    document.querySelectorAll(SELECTORS).forEach(el => {
        if (seen.has(el) || !isVisible(el)) return;
        seen.add(el);
        const rect = el.getBoundingClientRect();
        results.push({
            index: idx++,
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            name: el.getAttribute('name'),
            text: truncate(el.textContent, 200),
            ariaLabel: el.getAttribute('aria-label'),
            placeholder: el.getAttribute('placeholder'),
            role: el.getAttribute('role'),
            outerHTML: truncate(el.outerHTML, MAX_HTML),
            parentHTML: el.parentElement ? truncate(el.parentElement.outerHTML, MAX_PARENT) : null,
            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
        });
    });
    return JSON.stringify(results);
}
"""


class DOMSnapshot:
    """Captures and saves a snapshot of all interactable elements on a page."""

    async def capture(self, page: Page) -> list[dict]:
        """Return a list of interactable element descriptors from the live page."""
        raw = await page.evaluate(
            _EXTRACT_JS,
            INTERACTABLE_SELECTORS,
            OUTER_HTML_MAX_LEN,
            PARENT_HTML_MAX_LEN,
        )
        if isinstance(raw, str):
            return json.loads(raw)
        return raw or []

    async def save(self, page: Page, path: Path) -> list[dict]:
        """Capture and persist the snapshot to *path* as JSON."""
        elements = await self.capture(page)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(elements, indent=2), encoding="utf-8")
        return elements
