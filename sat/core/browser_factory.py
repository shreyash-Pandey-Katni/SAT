"""Browser factory — launches Playwright browsers (Chromium or Firefox)."""

from __future__ import annotations

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from sat.config import BrowserConfig


class BrowserFactory:
    """Creates and configures Playwright browser instances from config."""

    def __init__(self, config: BrowserConfig) -> None:
        self._config = config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> BrowserContext:
        """Launch the browser and return a fresh context."""
        self._playwright = await async_playwright().start()

        browser_type = (
            self._playwright.chromium
            if self._config.type in ("chromium", "chrome")
            else self._playwright.firefox
        )

        self._browser = await browser_type.launch(
            headless=self._config.headless,
            slow_mo=self._config.slow_mo,
            args=["--start-maximized"] if self._config.type == "chromium" else [],
        )

        context = await self._browser.new_context(
            viewport={
                "width": self._config.viewport_width,
                "height": self._config.viewport_height,
            },
            ignore_https_errors=True,
        )
        return context

    async def stop(self) -> None:
        """Stop browser and playwright instance."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def __aenter__(self) -> BrowserContext:
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
