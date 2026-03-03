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
                context = page.context

                # A preceding CLICK may have already opened a popup
                # (e.g. target="_blank").  Detect it by comparing the
                # real context pages against the tracked list from the
                # executor (which only contains explicitly-created pages).
                tracked_ids = set(id(p) for p in (all_pages or []))
                untracked = [
                    p for p in context.pages
                    if id(p) not in tracked_ids and not p.is_closed()
                ]

                if untracked:
                    popup = untracked[-1]
                    try:
                        await popup.wait_for_load_state(
                            "domcontentloaded", timeout=5000,
                        )
                    except Exception:
                        pass
                    return popup

                # No popup detected — create a new page explicitly
                new_page = await context.new_page()
                if url and url != "about:blank":
                    await new_page.goto(url, wait_until="domcontentloaded")
                return new_page

            case ActionType.SWITCH_TAB:
                target_url = action.value or ""
                target_title = (action.metadata or {}).get("title", "")

                # If the current page already matches the target, no-op.
                # This avoids an accidental switch when NEW_TAB already
                # adopted the popup and moved focus there.
                cur_url = page.url or ""
                if target_url and target_url in cur_url:
                    return None
                try:
                    cur_title = await page.title()
                except Exception:
                    cur_title = ""
                if target_title and target_title in cur_title:
                    return None

                # Search ALL context pages (includes browser-opened popups)
                real_pages = list(page.context.pages)
                found = await self._switch_tab(
                    page, real_pages, target_url, target_title,
                )
                if found is not None:
                    return found

            case ActionType.CLOSE_TAB:
                # Use real context pages so browser-opened popups are included
                remaining = [
                    p for p in page.context.pages
                    if p != page and not p.is_closed()
                ]
                await page.close()
                if remaining:
                    await remaining[-1].bring_to_front()
                    return remaining[-1]

            case ActionType.SCROLL:
                vp = action.viewport or {}
                scroll_x = vp.get("scrollX", 0)
                scroll_y = vp.get("scrollY", 0)
                await page.evaluate(f"window.scrollTo({scroll_x}, {scroll_y})")

            case ActionType.STORE:
                # STORE is a no-op in the traditional executor; the value
                # extraction is handled by CNLRunner at the higher level.
                pass

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
        element = await self._ensure_fillable(element)
        await element.fill(value)             # Clear existing text and type new value

    @staticmethod
    async def _ensure_fillable(element: ElementHandle) -> ElementHandle:
        """Return desired fillable element, drilling into wrappers if needed."""
        page = element.page if hasattr(element, "page") else None
        if page is None:
            return element
        js = """
        (el) => {
            const TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
            const isFillable = (e) =>
                TAGS.has(e.tagName) || e.isContentEditable;
            if (isFillable(el)) return null;
            const child = el.querySelector('input, textarea, select, [contenteditable]');
            if (child) return child;
            const lbl = el.closest('label');
            if (lbl) {
                const forId = lbl.getAttribute('for');
                if (forId) {
                    const target = document.getElementById(forId);
                    if (target && isFillable(target)) return target;
                }
                const nested = lbl.querySelector('input, textarea, select, [contenteditable]');
                if (nested) return nested;
            }
            let parent = el.parentElement;
            for (let i = 0; i < 3 && parent; i++, parent = parent.parentElement) {
                const found = parent.querySelector('input, textarea, select, [contenteditable]');
                if (found) return found;
            }
            return null;
        }
        """
        try:
            handle = await page.evaluate_handle(js, element)
            better = handle.as_element()
            if better:
                return better
        except Exception:
            pass
        return element

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
