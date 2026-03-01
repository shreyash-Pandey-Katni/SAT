"""Strategy 1 — Direct selector / locator matching.

Tries recorded selectors in priority order using Playwright's native locator API
(which is backed by CDP WebSocket — no polling).
"""

from __future__ import annotations

import logging

from playwright.async_api import ElementHandle, Error, Page, TimeoutError

from sat.core.models import RecordedAction, ResolutionMethod, SelectorInfo
from sat.executor.strategies.base import ResolutionStrategy

logger = logging.getLogger(__name__)


class SelectorStrategy(ResolutionStrategy):
    """Resolves elements using their recorded CSS/XPath/attribute selectors."""

    method = ResolutionMethod.SELECTOR

    def __init__(self, timeout_ms: int = 5000) -> None:
        self._timeout = timeout_ms

    async def resolve(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, float | None]:
        s = action.selector
        if s is None:
            return None, None

        for locator_expr in self._build_locators(page, s):
            if locator_expr is None:
                continue
            try:
                if hasattr(locator_expr, "wait_for"):
                    # Playwright Locator object
                    locator = locator_expr
                else:
                    locator = page.locator(str(locator_expr))

                await locator.wait_for(state="visible", timeout=self._timeout)
                count = await locator.count()
                if count == 1:
                    handle = await locator.element_handle()
                    if handle:
                        logger.debug("SelectorStrategy hit: %s", locator_expr)
                        return handle, None
                elif count > 1:
                    # Take first visible one
                    first = locator.first
                    handle = await first.element_handle()
                    if handle:
                        logger.debug("SelectorStrategy hit (first of %d): %s", count, locator_expr)
                        return handle, None
            except (TimeoutError, Error) as exc:
                logger.debug("Selector failed (%s): %s", locator_expr, exc)
                continue

        return None, None

    # ------------------------------------------------------------------

    def _build_locators(self, page: Page, s: SelectorInfo):
        """Yield locator expressions in priority order."""
        # 1. data-testid (most stable)
        if s.data_testid:
            yield page.locator(f'[data-testid="{s.data_testid}"]')

        # 2. ID
        if s.id:
            yield page.locator(f"#{_esc(s.id)}")

        # 3. Role + accessible name (Playwright semantic locator)
        if s.role and s.aria_label:
            yield page.get_by_role(s.role, name=s.aria_label, exact=True)  # type: ignore[arg-type]
        elif s.role and s.text_content:
            yield page.get_by_role(s.role, name=s.text_content, exact=True)  # type: ignore[arg-type]

        # 4. aria-label
        if s.aria_label:
            yield page.get_by_label(s.aria_label, exact=True)

        # 5. Placeholder
        if s.placeholder:
            yield page.get_by_placeholder(s.placeholder, exact=True)

        # 6. Exact text (for buttons / links)
        if s.text_content and s.tag_name in ("button", "a"):
            yield page.get_by_text(s.text_content, exact=True)

        # 7. name attribute
        if s.name:
            yield page.locator(f'[name="{s.name}"]')

        # 8. Recorded CSS selector
        if s.css:
            yield page.locator(s.css)

        # 9. XPath
        if s.xpath:
            yield page.locator(f"xpath={s.xpath}")


def _esc(val: str) -> str:
    """Minimal CSS identifier escaping for IDs that may contain special chars."""
    return val.replace(".", r"\.").replace(":", r"\:").replace("[", r"\[").replace("]", r"\]")
