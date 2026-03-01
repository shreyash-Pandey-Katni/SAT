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
    ) -> None:
        """Execute *action* on *element* (or page-level for navigate/tab actions).

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
                # The context handles new tab detection — skip during playback
                url = action.value or ""
                if url:
                    new_page = await page.context.new_page()
                    await new_page.goto(url, wait_until="domcontentloaded")

            case ActionType.SWITCH_TAB:
                if all_pages:
                    target_url = action.value or ""
                    target_title = (action.metadata or {}).get("title", "")
                    await self._switch_tab(page, all_pages, target_url, target_title)

            case ActionType.CLOSE_TAB:
                await page.close()

            case ActionType.SCROLL:
                vp = action.viewport or {}
                scroll_x = vp.get("scrollX", 0)
                scroll_y = vp.get("scrollY", 0)
                await page.evaluate(f"window.scrollTo({scroll_x}, {scroll_y})")

            case _:
                logger.warning("ActionPerformer: unknown action type %s", action.action_type)

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
    ) -> None:
        for p in all_pages:
            if p == current:
                continue
            page_url = p.url or ""
            page_title = await p.title()
            if target_url and target_url in page_url:
                await p.bring_to_front()
                return
            if target_title and target_title in page_title:
                await p.bring_to_front()
                return
        logger.warning("SWITCH_TAB: no matching tab found (url=%r title=%r)", target_url, target_title)
