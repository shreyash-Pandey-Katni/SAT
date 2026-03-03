"""DOMParser — extracts interactable elements from a live Playwright page
and returns them as structured Python data for the embedding strategy.
"""

from __future__ import annotations

import json

from playwright.async_api import Frame, Page

from sat.constants import INTERACTABLE_SELECTORS, OUTER_HTML_MAX_LEN, PARENT_HTML_MAX_LEN

# Accept both a top-level Page and a child Frame so callers can scope to iframes.
_PageOrFrame = Page | Frame

# JS that returns all interactable elements as a JSON string.
# The function receives three arguments injected by page.evaluate().
_EXTRACT_JS = """
([selectors, maxHtml, maxParent]) => {
    function truncate(s, n) {
        if (!s) return null;
        s = s.trim();
        return s.length > n ? s.substring(0, n) : s;
    }
    function isVisible(el) {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0')
            return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }
    const seen = new Set();
    const results = [];
    let idx = 0;
    document.querySelectorAll(selectors).forEach(el => {
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
            dataTestId: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
            role: el.getAttribute('role'),
            inputType: el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : null,
            href: el.getAttribute('href'),
            outerHTML: truncate(el.outerHTML, maxHtml),
            parentHTML: el.parentElement ? truncate(el.parentElement.outerHTML, maxParent) : null,
            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
        });
    });
    return JSON.stringify(results);
}
"""


class DOMParser:
    """Extracts a list of interactable element descriptors from a live page."""

    async def extract_candidates(
        self,
        page: Page | Frame,
        max_candidates: int = 50,
    ) -> list[dict]:
        """Return up to *max_candidates* visible, interactable element dicts."""
        raw: str = await page.evaluate(
            _EXTRACT_JS,
            [INTERACTABLE_SELECTORS, OUTER_HTML_MAX_LEN, PARENT_HTML_MAX_LEN],
        )
        if not raw:
            return []
        elements: list[dict] = json.loads(raw) if isinstance(raw, str) else raw
        return elements[:max_candidates]

    @staticmethod
    def build_html_description(el: dict) -> str:
        """Build a structured semantic description for embedding.

        Uses a ``key=value`` notation that mirrors the query format
        produced by :meth:`EmbeddingStrategy._build_query`, so that
        cosine similarity between matching pairs is maximised.
        """
        parts: list[str] = []
        tag = el.get("tag", "")
        if tag:
            parts.append(tag)
        if el.get("placeholder"):
            parts.append(f'placeholder="{el["placeholder"]}"')
        text = (el.get("text") or "").strip()
        if text:
            parts.append(f'text="{text[:80]}"')
        if el.get("ariaLabel"):
            parts.append(f'aria-label="{el["ariaLabel"]}"')
        if el.get("role"):
            parts.append(f'role={el["role"]}')
        if el.get("id"):
            parts.append(f'id={el["id"]}')
        if el.get("inputType"):
            parts.append(f'type={el["inputType"]}')
        if el.get("name"):
            parts.append(f'name={el["name"]}')
        if el.get("href"):
            parts.append(f'href="{el["href"][:80]}"')
        return " ".join(parts)
