"""EventListener — subscribes to CDP/Playwright events and routes them to
the Recorder via async callbacks.

All communication is push-based:
  * Browser → Python: via page.expose_function() (CDP channel)
  * Navigation / tab events: via page.on() (Playwright native events)
Zero polling anywhere.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine

from playwright.async_api import Frame, Page

from sat.recorder.action_builder import ActionBuilder
from sat.recorder.cnl_generator import CNLGenerator
from sat.recorder.dom_snapshot import DOMSnapshot
from sat.recorder.navigation_tracker import NavigationCausationTracker
from sat.recorder.selector_extractor import build_selector_from_event
from sat.core.models import RecordedAction

logger = logging.getLogger(__name__)

ActionCallback = Callable[[RecordedAction], Coroutine[Any, Any, None]]


class EventListener:
    """Attaches to a Playwright :class:`Page` and pushes actions to *on_action*."""

    def __init__(
        self,
        nav_tracker: NavigationCausationTracker,
        action_builder: ActionBuilder,
        cnl_generator: CNLGenerator,
        dom_snapshot: DOMSnapshot,
        on_action: ActionCallback,
        recordings_dir: Path,
        test_id: str,
        capture_screenshots: bool = True,
        capture_dom_snapshot: bool = True,
        auto_generate_cnl: bool = True,
    ) -> None:
        self._nav_tracker = nav_tracker
        self._builder = action_builder
        self._cnl_gen = cnl_generator
        self._dom_snapshot = dom_snapshot
        self._on_action = on_action
        self._recordings_dir = recordings_dir
        self._test_id = test_id
        self._capture_screenshots = capture_screenshots
        self._capture_dom_snapshot = capture_dom_snapshot
        self._auto_generate_cnl = auto_generate_cnl
        self._step_counter = 0
        self._pages: set[int] = set()           # track which pages are already wired

    # ------------------------------------------------------------------
    # Public setup
    # ------------------------------------------------------------------

    async def attach(self, page: Page) -> None:
        """Wire up all event listeners on *page*. Safe to call multiple times."""
        if id(page) in self._pages:
            return
        self._pages.add(id(page))

        # Expose Python handlers into the browser (CDP channel — not polling)
        await page.expose_function("__sat_click", self._make_click_handler(page))
        await page.expose_function("__sat_input", self._make_input_handler(page))
        await page.expose_function("__sat_select", self._make_select_handler(page))

        # Load capture.js into every frame (incl. iframes) via addInitScript
        capture_js = Path(__file__).parent / "capture.js"
        await page.add_init_script(path=str(capture_js))

        # Native Playwright events (CDP WebSocket, event-driven)
        page.on("framenavigated", self._make_navigate_handler(page))
        page.on("close", self._on_tab_close)

        # Newly opened tabs (from context)
        page.context.on("page", self._on_new_page)

    # ------------------------------------------------------------------
    # Internal helpers — return async callables bound to a specific page
    # ------------------------------------------------------------------

    def _make_click_handler(self, page: Page) -> Callable:
        async def handler(data: dict) -> None:
            step = self._next_step()
            url = page.url
            tab_id = str(id(page))

            self._nav_tracker.on_user_interaction("click", target_href=data.get("href"))

            scr_path, snap_path = await self._capture_artifacts(page, step)
            cnl = self._cnl_gen.generate(
                self._builder.build_click(data, step, url, tab_id)
            ) if self._auto_generate_cnl else None

            action = self._builder.build_click(
                data, step, url, tab_id, scr_path, snap_path, cnl
            )
            await self._emit(action)

        return handler

    def _make_input_handler(self, page: Page) -> Callable:
        async def handler(data: dict) -> None:
            step = self._next_step()
            url = page.url
            tab_id = str(id(page))

            self._nav_tracker.on_user_interaction("type")

            scr_path, snap_path = await self._capture_artifacts(page, step)
            cnl = self._cnl_gen.generate(
                self._builder.build_type(data, step, url, tab_id)
            ) if self._auto_generate_cnl else None

            action = self._builder.build_type(
                data, step, url, tab_id, scr_path, snap_path, cnl
            )
            await self._emit(action)

        return handler

    def _make_select_handler(self, page: Page) -> Callable:
        async def handler(data: dict) -> None:
            step = self._next_step()
            url = page.url
            tab_id = str(id(page))

            scr_path, snap_path = await self._capture_artifacts(page, step)
            cnl = self._cnl_gen.generate(
                self._builder.build_select(data, step, url, tab_id)
            ) if self._auto_generate_cnl else None

            action = self._builder.build_select(
                data, step, url, tab_id, scr_path, snap_path, cnl
            )
            await self._emit(action)

        return handler

    def _make_navigate_handler(self, page: Page) -> Callable:
        def handler(frame: Frame) -> None:
            if frame != page.main_frame:
                return                          # Ignore sub-frame navigations
            url = frame.url
            if not url or url == "about:blank":
                return
            if not self._nav_tracker.is_user_initiated(url):
                logger.debug("Navigation caused by interaction, skipping: %s", url)
                return

            step = self._next_step()
            tab_id = str(id(page))
            cnl = f'Navigate to "{url}";' if self._auto_generate_cnl else None
            action = self._builder.build_navigate(url, step, tab_id, cnl)
            asyncio.ensure_future(self._emit(action))

        return handler

    async def _on_new_page(self, page: Page) -> None:
        """Called instantly when a new tab/window opens."""
        step = self._next_step()
        tab_id = str(id(page))
        url = page.url or "about:blank"
        cnl = f'Open new tab "{url}";' if self._auto_generate_cnl else None
        action = self._builder.build_new_tab(url, step, tab_id, cnl)
        await self._emit(action)
        # Attach listeners to the new page too
        await self.attach(page)

    async def _on_tab_close(self, page: Page) -> None:
        """Called instantly when a tab closes."""
        step = self._next_step()
        tab_id = str(id(page))
        cnl = "Close current tab;" if self._auto_generate_cnl else None
        action = self._builder.build_close_tab(page.url, step, tab_id, cnl)
        await self._emit(action)
        self._pages.discard(id(page))

    # ------------------------------------------------------------------
    # Artifact capture helpers
    # ------------------------------------------------------------------

    async def _capture_artifacts(
        self, page: Page, step: int
    ) -> tuple[str | None, str | None]:
        """Capture screenshot + DOM snapshot, return their relative paths."""
        base = self._recordings_dir / self._test_id
        scr_path: str | None = None
        snap_path: str | None = None

        if self._capture_screenshots:
            scr_dir = base / "screenshots"
            scr_dir.mkdir(parents=True, exist_ok=True)
            scr_file = scr_dir / f"step_{step:04d}.png"
            try:
                await page.screenshot(path=str(scr_file), type="png")
                scr_path = str(scr_file.relative_to(self._recordings_dir.parent))
            except Exception as exc:
                logger.warning("Screenshot failed at step %d: %s", step, exc)

        if self._capture_dom_snapshot:
            snap_dir = base / "dom_snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_file = snap_dir / f"step_{step:04d}.json"
            try:
                await self._dom_snapshot.save(page, snap_file)
                snap_path = str(snap_file.relative_to(self._recordings_dir.parent))
            except Exception as exc:
                logger.warning("DOM snapshot failed at step %d: %s", step, exc)

        return scr_path, snap_path

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _next_step(self) -> int:
        self._step_counter += 1
        return self._step_counter

    async def _emit(self, action: RecordedAction) -> None:
        try:
            await self._on_action(action)
        except Exception as exc:
            logger.error("Error in action callback: %s", exc)
