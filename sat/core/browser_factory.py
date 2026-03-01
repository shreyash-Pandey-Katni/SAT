"""Browser factory — launches Playwright browsers (Chromium or Firefox)."""

from __future__ import annotations

import os
import shutil
import sys

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from sat.config import BrowserConfig

# Candidate system Chrome/Chromium binaries per platform (checked in order)
if sys.platform == "win32":
    _SYSTEM_CHROME_CANDIDATES = [
        "chrome",
        "chrome.exe",
    ]
    # Well-known install directories on Windows
    _WINDOWS_CHROME_PATHS = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
elif sys.platform == "darwin":
    _SYSTEM_CHROME_CANDIDATES = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome",
        "chromium",
    ]
    _WINDOWS_CHROME_PATHS = []
else:
    _SYSTEM_CHROME_CANDIDATES = [
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
    ]
    _WINDOWS_CHROME_PATHS = []


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

        chromium_extra: list[str] = []
        if sys.platform != "win32":
            chromium_extra.append("--disable-dev-shm-usage")  # /dev/shm OOM guard (Linux/macOS)

        launch_kwargs: dict = {
            "headless": self._config.headless,
            "slow_mo": self._config.slow_mo,
        }

        if self._config.type in ("chromium", "chrome"):
            launch_kwargs["args"] = chromium_extra

            # Resolve executable: explicit config > auto-detect system Chrome (headed mode)
            exe = self._config.executable_path.strip()
            if not exe and not self._config.headless:
                exe = self._find_system_chrome() or ""
            if exe:
                launch_kwargs["executable_path"] = exe

        self._browser = await browser_type.launch(**launch_kwargs)

        context = await self._browser.new_context(
            viewport={
                "width": self._config.viewport_width,
                "height": self._config.viewport_height,
            },
            ignore_https_errors=True,
        )
        return context

    async def stop(self) -> None:
        """Stop browser and playwright instance gracefully."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass  # Already closed or crashed — ignore
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @staticmethod
    def _find_system_chrome() -> str | None:
        """Return the path to a system-installed Chrome/Chromium binary, or None."""
        for candidate in _SYSTEM_CHROME_CANDIDATES:
            path = shutil.which(candidate)
            if path:
                return path
        # On Windows, also check well-known install directories
        for path_str in _WINDOWS_CHROME_PATHS:
            if os.path.isfile(path_str):
                return path_str
        return None

    async def __aenter__(self) -> BrowserContext:
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
