"""Strategy 1 — Direct selector / locator matching.

Tries recorded selectors in priority order using Playwright's native locator API
(which is backed by CDP WebSocket — no polling).

Frame / shadow DOM routing
--------------------------
* If ``s.frame_url`` is set the element lives inside an iframe.  We resolve the
  frame first and run all locators against it instead of the top-level page.
* Playwright's CSS engine **automatically pierces shadow roots** when evaluating
  locators, so the shadow-boundary-aware CSS paths produced by ``capture.js``
  work without any extra handling.  The ``s.in_shadow_dom`` flag is stored for
  diagnostics; XPath is skipped because XPath cannot pierce shadow roots.
"""

from __future__ import annotations

import logging

from playwright.async_api import ElementHandle, Error, Frame, Page, TimeoutError

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

        # ── iframe routing ────────────────────────────────────────────────
        # If the element was recorded inside an iframe, resolve that frame first
        # and run all locators on the frame rather than the top-level page.
        root: Page | Frame = page
        if s.frame_url:
            frame = page.frame(url=s.frame_url)
            if frame is None:
                # Fall back: try matching by any frame whose URL starts with frame_url
                for f in page.frames:
                    if f.url.startswith(s.frame_url) or s.frame_url.startswith(f.url):
                        frame = f
                        break
            if frame is not None:
                root = frame
                logger.debug("SelectorStrategy: scoped to iframe %s", s.frame_url)
            else:
                logger.warning(
                    "SelectorStrategy: iframe not found for url=%s, trying top-level",
                    s.frame_url,
                )

        for locator_expr in self._build_locators(root, s):
            if locator_expr is None:
                continue
            try:
                if hasattr(locator_expr, "wait_for"):
                    locator = locator_expr
                else:
                    locator = root.locator(str(locator_expr))

                await locator.wait_for(state="visible", timeout=self._timeout)
                count = await locator.count()
                if count == 1:
                    handle = await locator.element_handle()
                    if handle:
                        logger.debug("SelectorStrategy hit: %s", locator_expr)
                        return handle, None
                elif count > 1:
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

    def _build_locators(self, root: Page | Frame, s: SelectorInfo):
        """Yield locator expressions in priority order.

        Shadow-DOM note: Playwright's CSS engine pierces shadow roots automatically
        so all ``root.locator(css)`` calls below work for shadow-hosted elements
        without any extra ``>>`` handling.
        """
        # 1. data-testid (most stable)
        if s.data_testid:
            yield root.locator(f'[data-testid="{s.data_testid}"]')

        # 2. ID
        if s.id:
            yield root.locator(f"#{_esc(s.id)}")

        # 3. Role + accessible name (Playwright semantic locator)
        if s.role and s.aria_label:
            yield root.get_by_role(s.role, name=s.aria_label, exact=True)  # type: ignore[arg-type]
        elif s.role and s.text_content:
            yield root.get_by_role(s.role, name=s.text_content, exact=True)  # type: ignore[arg-type]

        # 4. aria-label
        if s.aria_label:
            yield root.get_by_label(s.aria_label, exact=True)

        # 5. Placeholder
        if s.placeholder:
            yield root.get_by_placeholder(s.placeholder, exact=True)

        # 6. Exact text (for buttons / links)
        if s.text_content and s.tag_name in ("button", "a"):
            yield root.get_by_text(s.text_content, exact=True)

        # 7. name attribute
        if s.name:
            yield root.locator(f'[name="{s.name}"]')

        # 8. Recorded CSS selector (Playwright's CSS engine pierces shadow roots)
        if s.css:
            yield root.locator(s.css)

        # 9. XPath — only for non-shadow-DOM elements (XPath can't pierce shadow roots)
        if s.xpath and not s.in_shadow_dom:
            yield root.locator(f"xpath={s.xpath}")


def _esc(val: str) -> str:
    """Minimal CSS identifier escaping for IDs that may contain special chars."""
    return val.replace(".", r"\.").replace(":", r"\:").replace("[", r"\[").replace("]", r"\]")
