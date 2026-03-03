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
        """Build a semantic description for the embedding stage.

        **Form controls** (``<input>``, ``<select>``, ``<textarea>``) use a
        structured ``key=value`` format — this aligns with the query produced
        by :meth:`EmbeddingStrategy._build_query` and scores 0.88+ for exact
        label matches.

        **Text-bearing elements** (buttons, links, nav items, divs with text)
        use just their cleaned visible text, because the structured format
        causes ``nomic-embed-text`` to cluster all structurally similar short
        strings together and lose per-element discrimination.
        """
        import re as _re
        tag = el.get("tag", "")
        placeholder = el.get("placeholder", "") or ""
        text_raw = (el.get("text") or "").strip()

        # Form controls: structured format
        if tag in ("input", "select", "textarea") or placeholder:
            parts: list[str] = []
            if tag:
                parts.append(tag)
            if placeholder:
                parts.append(f'placeholder="{placeholder}"')
            input_type = el.get("inputType", "")
            if input_type and input_type != "text":
                parts.append(f"type={input_type}")
            aria = el.get("ariaLabel") or ""
            if aria:
                parts.append(f'label="{aria}"')
            name = el.get("name") or ""
            if name:
                parts.append(f"name={name}")
            return " ".join(parts)

        # Text-bearing elements: stripped visible text only
        if text_raw:
            # Remove leading emoji / icon characters and trailing arrows
            _noise = _re.compile(
                r'^[\U0001F000-\U0001FFFF\u2190-\u27BF\u2600-\u26FF\u2700-\u27BF]+\s*'
                r'|\s*[\u25BC\u25B2\u25C4\u25BA▼▲◄►]+\s*$'
            )
            _collapse = _re.compile(r'\s+')
            clean = _noise.sub('', text_raw).strip()
            clean = _collapse.sub(' ', clean).strip()[:120]
            if clean:
                return clean
            return text_raw[:120]

        # Fallback
        return tag
