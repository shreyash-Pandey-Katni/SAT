"""PlaywrightManager — lifecycle wrapper around a Playwright browser context.

All browser ↔ agent communication happens over CDP WebSocket (zero polling).
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from playwright.async_api import BrowserContext, Page

from sat.config import SATConfig
from sat.core.browser_factory import BrowserFactory


class PlaywrightManager:
    """Manages a single browser context and its pages."""

    def __init__(self, config: SATConfig) -> None:
        self._config = config
        self._factory = BrowserFactory(config.browser)
        self._context: BrowserContext | None = None
        self._pages: dict[str, Page] = {}   # tab_id → Page
        self._active_tab_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, url: str | None = None) -> Page:
        """Start the browser, open a first page, optionally navigate to *url*."""
        self._context = await self._factory.start()
        page = await self._context.new_page()
        tab_id = self._page_id(page)
        self._pages[tab_id] = page
        self._active_tab_id = tab_id

        if url:
            await page.goto(url, wait_until="load")
            # Wait for network to be idle to ensure dynamic content loads
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                # Continue if timeout - page should be mostly ready
                pass

        return page

    async def stop(self) -> None:
        try:
            await self._factory.stop()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Playwright stop warning (non-fatal): %s", exc)
        finally:
            self._pages.clear()
            self._active_tab_id = None

    # ------------------------------------------------------------------
    # Page management
    # ------------------------------------------------------------------

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("PlaywrightManager not started — call await start() first.")
        return self._context

    @property
    def active_page(self) -> Page:
        if self._active_tab_id is None or self._active_tab_id not in self._pages:
            raise RuntimeError("No active page; start the manager first.")
        return self._pages[self._active_tab_id]

    def get_page(self, tab_id: str) -> Page | None:
        return self._pages.get(tab_id)

    def all_pages(self) -> list[Page]:
        return list(self._pages.values())

    async def new_page(self, url: str | None = None) -> Page:
        page = await self._context.new_page()
        tab_id = self._page_id(page)
        self._pages[tab_id] = page
        self._active_tab_id = tab_id
        if url:
            await page.goto(url, wait_until="load")
            # Wait for network to be idle
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
        return page

    def switch_to(self, tab_id: str) -> Page:
        if tab_id not in self._pages:
            raise KeyError(f"No page with tab_id={tab_id!r}")
        self._active_tab_id = tab_id
        return self._pages[tab_id]

    def remove_page(self, page: Page) -> None:
        tab_id = self._page_id(page)
        self._pages.pop(tab_id, None)
        if self._active_tab_id == tab_id:
            self._active_tab_id = next(iter(self._pages), None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _page_id(page: Page) -> str:
        """Stable identifier — Playwright Page objects have a URL + creation order."""
        return str(id(page))

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "PlaywrightManager":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
