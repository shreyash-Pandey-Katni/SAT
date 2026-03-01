"""Recorder — main orchestrator for a recording session.

Usage::

    recorder = Recorder(config)
    test = await recorder.record(
        start_url="https://example.com",
        name="My Test",
    )
    # test is a RecordedTest ready to be saved
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Any

from playwright.async_api import Page

from sat.config import SATConfig
from sat.core.models import RecordedAction, RecordedTest
from sat.core.playwright_manager import PlaywrightManager
from sat.recorder.action_builder import ActionBuilder
from sat.recorder.cnl_generator import CNLGenerator
from sat.recorder.dom_snapshot import DOMSnapshot
from sat.recorder.event_listener import EventListener
from sat.recorder.navigation_tracker import NavigationCausationTracker

logger = logging.getLogger(__name__)

LiveCallback = Callable[[RecordedAction], Coroutine[Any, Any, None]]


class Recorder:
    """Manages a complete browser recording session."""

    def __init__(self, config: SATConfig) -> None:
        self._config = config
        self._recordings_dir = Path(config.recorder.output_dir)
        self._actions: list[RecordedAction] = []
        self._test_id: str = ""
        self._live_callbacks: list[LiveCallback] = []
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_action(self, callback: LiveCallback) -> None:
        """Register a callback invoked for every recorded action (for live feeds)."""
        self._live_callbacks.append(callback)

    async def record(
        self,
        start_url: str,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> RecordedTest:
        """Open a browser, record user interactions until stop() is called.

        Returns the completed :class:`RecordedTest`.
        """
        self._test_id = str(uuid.uuid4())
        self._actions = []
        self._stop_event.clear()

        manager = PlaywrightManager(self._config)
        page = await manager.start(url=start_url)

        nav_tracker = NavigationCausationTracker(
            self._config.recorder.navigation_causation_window_ms
        )
        listener = EventListener(
            nav_tracker=nav_tracker,
            action_builder=ActionBuilder(),
            cnl_generator=CNLGenerator(),
            dom_snapshot=DOMSnapshot(),
            on_action=self._handle_action,
            recordings_dir=self._recordings_dir,
            test_id=self._test_id,
            capture_screenshots=self._config.recorder.capture_screenshots,
            capture_dom_snapshot=self._config.recorder.capture_dom_snapshot,
            auto_generate_cnl=self._config.recorder.auto_generate_cnl,
        )
        await listener.attach(page)

        logger.info("Recording started — test_id=%s  url=%s", self._test_id, start_url)
        logger.info("Interact with the browser. Call recorder.stop() to finish.")

        # Block until stop() is called (e.g. from CLI signal or Web UI)
        await self._stop_event.wait()

        # Drain: give in-flight CDP event callbacks a short window to finish
        # so we don't lose the last action(s) recorded just before Ctrl+C.
        await asyncio.sleep(0.3)

        # Gracefully close browser — ignore errors if it's already closing
        try:
            await manager.stop()
        except Exception as exc:
            logger.debug("Browser close warning (non-fatal): %s", exc)

        # Build and persist the test
        test = self._build_test(
            name=name,
            description=description,
            start_url=start_url,
            browser=self._config.browser.type,
            tags=tags or [],
        )
        await self._save_test(test)
        logger.info("Recording saved — %d actions  path=%s", len(test.actions), self._test_path(self._test_id))
        return test

    def stop(self) -> None:
        """Signal the recorder to finish and save."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _handle_action(self, action: RecordedAction) -> None:
        self._actions.append(action)
        logger.debug("[step %d] %s  %s", action.step_number, action.action_type.value, action.url)
        for cb in self._live_callbacks:
            try:
                await cb(action)
            except Exception as exc:
                logger.error("Live callback error: %s", exc)

    def _build_test(
        self,
        name: str,
        description: str,
        start_url: str,
        browser: str,
        tags: list[str],
    ) -> RecordedTest:
        # Re-number steps sequentially (they should already be, but ensure it)
        for i, action in enumerate(self._actions, start=1):
            action.step_number = i

        cnl_lines = [a.cnl_step for a in self._actions if a.cnl_step]
        cnl_text = "\n".join(cnl_lines) or None

        return RecordedTest(
            id=self._test_id,
            name=name,
            description=description,
            created_at=datetime.utcnow(),
            start_url=start_url,
            browser=browser,
            actions=list(self._actions),
            tags=tags,
            cnl=cnl_text,
            cnl_steps=[],    # populated by CNL parser if needed
        )

    async def _save_test(self, test: RecordedTest) -> None:
        test_dir = self._recordings_dir / test.id
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / "test.json"
        test_file.write_text(test.model_dump_json(indent=2), encoding="utf-8")

    def _test_path(self, test_id: str) -> Path:
        return self._recordings_dir / test_id / "test.json"
