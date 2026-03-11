"""Executor — main orchestrator for intelligent test replay.

Strategy chain: Selector → Embedding → OCR → VLM → Fail
Auto-heal: updates test file when fallback strategies succeed.
Event-driven: Playwright auto-wait, no polling.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from playwright.async_api import Page

from sat.config import SATConfig
from sat.core.models import (
    ActionType,
    ExecutionStepResult,
    RecordedAction,
    RecordedTest,
    ResolutionMethod,
    StepResult,
)
from sat.core.playwright_manager import PlaywrightManager
from sat.executor.action_performer import ActionPerformer
from sat.executor.auto_healer import AutoHealer
from sat.executor.report import ReportGenerator
from sat.executor.strategies.embedding_strategy import EmbeddingStrategy
from sat.executor.strategies.ocr_strategy import OCRStrategy
from sat.executor.strategies.selector_strategy import SelectorStrategy
from sat.executor.strategies.vlm_strategy import VLMStrategy
from sat.executor.strategy_chain import StrategyChain
from sat.core.models import ExecutionReport

logger = logging.getLogger(__name__)

StepCallback = Callable[[ExecutionStepResult], Coroutine[Any, Any, None]]


class Executor:
    """Replays a :class:`RecordedTest` using an intelligent fallback chain."""

    def __init__(self, config: SATConfig) -> None:
        self._config = config
        self._recordings_dir = Path(config.recorder.output_dir)
        self._step_callbacks: list[StepCallback] = []

        # Build strategy chain from config
        ec = config.executor
        strategies_map = {
            "selector": lambda: SelectorStrategy(timeout_ms=ec.selector.timeout_ms),
            "embedding": lambda: EmbeddingStrategy(config=ec.embedding),
            "ocr": lambda: OCRStrategy(config=ec.ocr),
            "vlm": lambda: VLMStrategy(config=ec.vlm),
        }
        self._strategy_chain = StrategyChain([
            strategies_map[name]()
            for name in ec.strategies
            if name in strategies_map
        ])
        self._performer = ActionPerformer()
        self._healer = AutoHealer(enabled=ec.auto_heal)
        self._reporter = ReportGenerator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_step_complete(self, callback: StepCallback) -> None:
        """Register a callback invoked after each step (for live UI feed)."""
        self._step_callbacks.append(callback)

    async def execute(self, test: RecordedTest) -> ExecutionReport:
        """Replay all actions in *test* and return a full :class:`ExecutionReport`."""
        test_path = self._recordings_dir / test.id / "test.json"
        reports_dir = self._recordings_dir / test.id / "reports"
        screenshots_dir = self._recordings_dir / test.id / "exec_screenshots"

        manager = PlaywrightManager(self._config)
        page = await manager.start(url=test.start_url)
        self._active_page: Page = page         # tracks focus across tab actions

        started_at = datetime.now(UTC)
        step_results: list[ExecutionStepResult] = []

        for action in test.actions:
            result = await self._execute_step(
                page=self._active_page,
                action=action,
                test=test,
                test_path=test_path,
                screenshots_dir=screenshots_dir,
                all_pages=manager.all_pages(),
            )
            step_results.append(result)

            for cb in self._step_callbacks:
                try:
                    await cb(result)
                except Exception as exc:
                    logger.error("Step callback error: %s", exc)

        await manager.stop()

        report = self._reporter.build(
            config=self._config,
            test_id=test.id,
            test_name=test.name,
            start_url=test.start_url,
            steps=step_results,
            started_at=started_at,
        )
        report_path = self._reporter.save(report, reports_dir)
        logger.info(
            "Execution complete — passed=%d failed=%d healed=%d  report=%s",
            report.passed, report.failed, report.healed_steps, report_path,
        )
        return report

    # ------------------------------------------------------------------
    # Single step execution
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        page: Page,
        action: RecordedAction,
        test: RecordedTest,
        test_path: Path,
        screenshots_dir: Path,
        all_pages: list[Page],
    ) -> ExecutionStepResult:
        start_ms = time.monotonic() * 1000

        logger.info(
            "[step %d/%d] %s  url=%s",
            action.step_number, len(test.actions), action.action_type.value, action.url,
        )

        expected_url = action.url or None

        # ── Tab / nav actions don't need element resolution ──────────────
        if action.action_type in (
            ActionType.NAVIGATE,
            ActionType.NEW_TAB,
            ActionType.SWITCH_TAB,
            ActionType.CLOSE_TAB,
            ActionType.SCROLL,
            ActionType.STORE,
        ):
            try:
                new_page = await self._performer.perform(page, None, action, all_pages)
                # Update the active page when a tab action changes focus
                if new_page is not None:
                    self._active_page = new_page
                    page = new_page
                await self._wait_stable(page)
                return self._ok(
                    action,
                    ResolutionMethod.NONE,
                    None,
                    False,
                    start_ms,
                    expected_url=expected_url,
                    actual_url=page.url,
                )
            except Exception as exc:
                return self._fail(
                    action,
                    str(exc),
                    start_ms,
                    expected_url=expected_url,
                    actual_url=page.url,
                )

        # ── Ensure we are on the correct page for this action ────────────
        # Recording order may differ from execution order (e.g. a click
        # that opens a popup records NEW_TAB/SWITCH_TAB *before* the
        # CLICK itself because context.on("page") fires asynchronously).
        # Compare action.url to the current page and switch if needed.
        page = await self._ensure_correct_page(page, action)

        # ── Element actions: resolve → perform → heal ────────────────────
        element, method, score, resolution_trace = await self._strategy_chain.resolve_element_with_trace(page, action)

        if element is None:
            return self._fail(
                action,
                "Element not found — all strategies exhausted",
                start_ms,
                resolution_method=ResolutionMethod.NONE,
                expected_url=expected_url,
                actual_url=page.url,
                resolution_trace=resolution_trace,
            )

        # Perform the action
        try:
            await self._performer.perform(page, element, action, all_pages)
            await self._wait_stable(page)
        except Exception as exc:
            return self._fail(
                action,
                str(exc),
                start_ms,
                resolution_method=method,
                expected_url=expected_url,
                actual_url=page.url,
                resolution_trace=resolution_trace,
            )

        # Auto-heal if we used a fallback strategy
        healed = False
        if method not in (ResolutionMethod.SELECTOR, ResolutionMethod.NONE):
            healed = await self._healer.heal(
                page, action, element, method, score, test, test_path
            )

        # Capture post-action screenshot for report
        scr_path = await self._screenshot(page, screenshots_dir, action.step_number)

        return ExecutionStepResult(
            step_number=action.step_number,
            action=action,
            cnl_step=action.cnl_step,
            result=StepResult.PASSED,
            resolution_method=method,
            similarity_score=score,
            expected_url=expected_url,
            actual_url=page.url,
            resolution_trace=resolution_trace,
            duration_ms=int(time.monotonic() * 1000 - start_ms),
            screenshot_path=scr_path,
            healed=healed,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_correct_page(self, page: Page, action: RecordedAction) -> Page:
        """Return the context page whose URL matches ``action.url``.

        During recording, async events (e.g. ``context.on("page")``) may
        cause NEW_TAB / SWITCH_TAB to be recorded *before* the CLICK that
        triggered them.  When the executor replays in order, ``_active_page``
        may point to the *new* tab while the next CLICK still belongs to the
        *original* tab.

        This method detects the mismatch and transparently brings the
        correct page to the front so element resolution succeeds.
        """
        action_url = action.url or ""
        if not action_url:
            return page

        # Fast path — current page already matches
        cur_url = page.url or ""
        if action_url in cur_url or cur_url in action_url:
            return page

        # Search all live context pages for one with a matching URL
        for p in page.context.pages:
            if p.is_closed():
                continue
            p_url = p.url or ""
            if action_url in p_url or p_url in action_url:
                logger.info(
                    "[step %d] Page mismatch — switching from %r to %r for action",
                    action.step_number, cur_url, p_url,
                )
                await p.bring_to_front()
                self._active_page = p
                return p

        # No match found — stay on the current page (best effort)
        logger.warning(
            "[step %d] Could not find a page matching url=%r; "
            "staying on current page %r",
            action.step_number, action_url, cur_url,
        )
        return page

    async def _wait_stable(self, page: Page) -> None:
        mode = self._config.executor.wait_after_action
        if mode == "none":
            return
        try:
            # Short timeout: if the page hasn't settled by now the action
            # likely didn't trigger a navigation.  Avoids the old 5 s
            # penalty that `networkidle` caused on every single step.
            await page.wait_for_load_state(mode, timeout=2000)  # type: ignore[arg-type]
        except Exception:
            pass  # Timeout is acceptable — page may already be stable

    async def _screenshot(
        self, page: Page, directory: Path, step: int
    ) -> str | None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"step_{step:04d}.png"
            await page.screenshot(path=str(path), type="png")
            # Use as_posix() to ensure forward slashes on all platforms
            # This ensures paths work correctly in web UI and JSON reports
            return path.as_posix()
        except Exception as exc:
            logger.debug("Post-execution screenshot failed: %s", exc)
            return None

    @staticmethod
    def _ok(
        action: RecordedAction,
        method: ResolutionMethod,
        score: float | None,
        healed: bool,
        start_ms: float,
        screenshot_path: str | None = None,
        expected_url: str | None = None,
        actual_url: str | None = None,
        resolution_trace: list[ExecutionStepResult.ResolutionAttempt] | None = None,
    ) -> ExecutionStepResult:
        return ExecutionStepResult(
            step_number=action.step_number,
            action=action,
            cnl_step=action.cnl_step,
            result=StepResult.PASSED,
            resolution_method=method,
            similarity_score=score,
            expected_url=expected_url,
            actual_url=actual_url,
            resolution_trace=resolution_trace or [],
            duration_ms=int(time.monotonic() * 1000 - start_ms),
            screenshot_path=screenshot_path,
            healed=healed,
        )

    @staticmethod
    def _fail(
        action: RecordedAction,
        error: str,
        start_ms: float,
        resolution_method: ResolutionMethod = ResolutionMethod.NONE,
        expected_url: str | None = None,
        actual_url: str | None = None,
        resolution_trace: list[ExecutionStepResult.ResolutionAttempt] | None = None,
    ) -> ExecutionStepResult:
        logger.error(
            "[step %d] FAILED — %s", action.step_number, error
        )
        return ExecutionStepResult(
            step_number=action.step_number,
            action=action,
            cnl_step=action.cnl_step,
            result=StepResult.FAILED,
            resolution_method=resolution_method,
            expected_url=expected_url,
            actual_url=actual_url,
            resolution_trace=resolution_trace or [],
            error=error,
            duration_ms=int(time.monotonic() * 1000 - start_ms),
        )
