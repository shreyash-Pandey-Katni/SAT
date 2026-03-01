"""ActionPerformer — executes a recorded action on a resolved element or page."""

from __future__ import annotations

import logging

from playwright.async_api import ElementHandle, Page

from sat.core.models import ActionType, RecordedAction

logger = logging.getLogger(__name__)


class ActionPerformer:
    """Performs the actual browser interaction after element resolution."""

    async def perform(
        self,
        page: Page,
        element: ElementHandle | None,
        action: RecordedAction,
        all_pages: list[Page] | None = None,
    ) -> Page | None:
        """Execute *action* on *element* (or page-level for navigate/tab actions).

        Returns a :class:`Page` when the active page changes (NEW_TAB,
        SWITCH_TAB, CLOSE_TAB) so the caller can update its reference.
        Returns ``None`` when the active page is unchanged.

        Playwright auto-waits after each action — no explicit sleeps needed.
        """
        match action.action_type:
            case ActionType.CLICK:
                await self._click(element, page, action)

            case ActionType.TYPE:
                await self._type(element, action)

            case ActionType.SELECT:
                await self._select(element, action)

            case ActionType.HOVER:
                if element:
                    await element.hover()

            case ActionType.NAVIGATE:
                url = action.value or action.url
                await page.goto(url, wait_until="domcontentloaded")

            case ActionType.NEW_TAB:
                url = action.value or ""
                new_page = await page.context.new_page()
                if url and url != "about:blank":
                    await new_page.goto(url, wait_until="domcontentloaded")
                return new_page

            case ActionType.SWITCH_TAB:
                if all_pages:
                    target_url = action.value or ""
                    target_title = (action.metadata or {}).get("title", "")
                    found = await self._switch_tab(page, all_pages, target_url, target_title)
                    if found is not None:
                        return found

            case ActionType.CLOSE_TAB:
                remaining = [p for p in (all_pages or []) if p != page]
                await page.close()
                if remaining:
                    await remaining[-1].bring_to_front()
                    return remaining[-1]

            case ActionType.SCROLL:
                vp = action.viewport or {}
                scroll_x = vp.get("scrollX", 0)
                scroll_y = vp.get("scrollY", 0)
                await page.evaluate(f"window.scrollTo({scroll_x}, {scroll_y})")

            case _:
                logger.warning("ActionPerformer: unknown action type %s", action.action_type)

        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _click(
        self, element: ElementHandle | None, page: Page, action: RecordedAction
    ) -> None:
        if element:
            await element.click()
        else:
            # Coordinate fallback (used after VLM strategy)
            pos = action.element_position or {}
            x = pos.get("x", 0) + pos.get("width", 0) / 2
            y = pos.get("y", 0) + pos.get("height", 0) / 2
            await page.mouse.click(x, y)

    async def _type(self, element: ElementHandle | None, action: RecordedAction) -> None:
        if element is None:
            raise RuntimeError("Cannot TYPE: element is None")
        value = action.value or ""
        await element.fill(value)             # Clear existing text and type new value

    async def _select(self, element: ElementHandle | None, action: RecordedAction) -> None:
        if element is None:
            raise RuntimeError("Cannot SELECT: element is None")
        value = action.value or ""
        await element.select_option(value=value)

    async def _switch_tab(
        self, current: Page, all_pages: list[Page], target_url: str, target_title: str
    ) -> Page | None:
        """Find and focus the tab matching *target_url* or *target_title*.

        Returns the matching :class:`Page` or ``None`` if not found.
        """
        for p in all_pages:
            if p == current:
                continue
            page_url = p.url or ""
            page_title = await p.title()
            if target_url and target_url in page_url:
                await p.bring_to_front()
                return p
            if target_title and target_title in page_title:
                await p.bring_to_front()
                return p
        # Fallback: if there's only one other tab, switch to it
        others = [p for p in all_pages if p != current]
        if len(others) == 1:
            await others[0].bring_to_front()
            return others[0]
        logger.warning("SWITCH_TAB: no matching tab found (url=%r title=%r)", target_url, target_title)
        return None
