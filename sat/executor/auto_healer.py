"""AutoHealer — updates recorded selectors after a fallback strategy succeeds.

When the Selector strategy fails but Embedding or VLM finds the element,
we update the test file with the new selectors so future runs use
direct lookups again.  Atomic write (tmp → rename) prevents corruption.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import ElementHandle, Page

from sat.core.models import (
    HealRecord,
    RecordedAction,
    RecordedTest,
    ResolutionMethod,
    SelectorInfo,
)

logger = logging.getLogger(__name__)

# JS to extract fresh selector info from a live element
_EXTRACT_SELECTOR_JS = """
(el) => {
    function computeSelector(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        const parts = [];
        let node = el;
        while (node && node.tagName !== 'BODY') {
            let sel = node.tagName.toLowerCase();
            if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
            let nth = 1;
            let sib = node.previousSibling;
            while (sib) {
                if (sib.nodeType === 1 && sib.tagName === node.tagName) nth++;
                sib = sib.previousSibling;
            }
            sel += ':nth-of-type(' + nth + ')';
            parts.unshift(sel);
            node = node.parentElement;
        }
        return parts.join(' > ');
    }
    function computeXPath(el) {
        const parts = [];
        let node = el;
        while (node && node.nodeType === 1) {
            let idx = 1, sib = node.previousSibling;
            while (sib) {
                if (sib.nodeType === 1 && sib.nodeName === node.nodeName) idx++;
                sib = sib.previousSibling;
            }
            parts.unshift(node.nodeName.toLowerCase() + '[' + idx + ']');
            node = node.parentElement;
        }
        return '/' + parts.join('/');
    }
    return {
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        className: (el.className || '').substring(0, 200),
        name: el.getAttribute('name'),
        text: (el.textContent || '').trim().substring(0, 200),
        ariaLabel: el.getAttribute('aria-label'),
        placeholder: el.getAttribute('placeholder'),
        dataTestId: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
        href: el.getAttribute('href'),
        role: el.getAttribute('role'),
        inputType: el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : null,
        outerHTML: el.outerHTML.substring(0, 500),
        parentHTML: el.parentElement ? el.parentElement.outerHTML.substring(0, 300) : null,
        css: computeSelector(el),
        xpath: computeXPath(el),
    };
}
"""


class AutoHealer:
    """Patches selector info in a :class:`RecordedTest` after a fallback succeeds."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    async def heal(
        self,
        page: Page,
        action: RecordedAction,
        element: ElementHandle,
        method: ResolutionMethod,
        score: float | None,
        test: RecordedTest,
        test_path: Path,
    ) -> bool:
        """Update *action* with fresh selectors from *element* and persist *test*.

        Returns True if a heal was performed, False otherwise.
        """
        if not self._enabled:
            return False
        if method == ResolutionMethod.SELECTOR:
            return False  # No heal needed — original selectors still work
        if action.selector is None:
            return False

        # Extract fresh selectors from the live element
        try:
            data: dict = await page.evaluate(_EXTRACT_SELECTOR_JS, element)
        except Exception as exc:
            logger.warning("AutoHealer: failed to extract selector data: %s", exc)
            return False

        new_selector = SelectorInfo(
            tag_name=data.get("tag", "unknown"),
            css=data.get("css"),
            xpath=data.get("xpath"),
            id=data.get("id") or None,
            name=data.get("name"),
            class_name=data.get("className") or None,
            text_content=data.get("text") or None,
            aria_label=data.get("ariaLabel"),
            placeholder=data.get("placeholder"),
            data_testid=data.get("dataTestId"),
            href=data.get("href"),
            role=data.get("role"),
            input_type=data.get("inputType"),
            outer_html_snippet=data.get("outerHTML", ""),
            parent_html_snippet=data.get("parentHTML"),
        )

        heal_record = HealRecord(
            healed_at=datetime.utcnow(),
            healed_by=method.value,
            similarity_score=score,
            previous_selector=action.selector.model_copy(),
            new_selector=new_selector,
        )
        action.heal_history.append(heal_record)
        action.selector = new_selector
        action.last_healed = datetime.utcnow()

        # Persist atomically
        await self._atomic_save(test, test_path)

        logger.info(
            "AutoHealed step %d via %s (score=%s)  new_css=%r",
            action.step_number,
            method.value,
            f"{score:.4f}" if score is not None else "N/A",
            new_selector.css,
        )
        return True

    # ------------------------------------------------------------------

    @staticmethod
    async def _atomic_save(test: RecordedTest, path: Path) -> None:
        """Write test JSON to a temp file then rename atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, prefix=".tmp_", suffix=".json"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(test.model_dump_json(indent=2))
            os.replace(tmp_path, path)          # atomic on POSIX
        except Exception:
            os.unlink(tmp_path)
            raise
