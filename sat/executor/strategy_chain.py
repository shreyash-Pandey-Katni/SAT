"""StrategyChain — runs resolution strategies in priority order until one succeeds."""

from __future__ import annotations

import logging
import time

from playwright.async_api import ElementHandle, Page

from sat.core.models import ExecutionStepResult, RecordedAction, ResolutionMethod
from sat.executor.strategies.base import ResolutionStrategy

logger = logging.getLogger(__name__)


class StrategyChain:
    """Executes strategies in order, returning the first successful result."""

    def __init__(self, strategies: list[ResolutionStrategy]) -> None:
        self._strategies = strategies

    async def resolve_element_with_trace(
        self, page: Page, action: RecordedAction
    ) -> tuple[
        ElementHandle | None,
        ResolutionMethod,
        float | None,
        list[ExecutionStepResult.ResolutionAttempt],
    ]:
        """Try each strategy in sequence and return a full attempt trace."""
        trace: list[ExecutionStepResult.ResolutionAttempt] = []

        for strategy in self._strategies:
            logger.debug("Trying strategy: %s", strategy.method.value)
            strategy_start_ms = time.monotonic() * 1000

            try:
                element, score = await strategy.resolve(page, action)
                duration_ms = int(time.monotonic() * 1000 - strategy_start_ms)
            except Exception as exc:
                duration_ms = int(time.monotonic() * 1000 - strategy_start_ms)
                logger.error(
                    "Strategy %s raised an exception: %s", strategy.method.value, exc
                )
                trace.append(
                    ExecutionStepResult.ResolutionAttempt(
                        strategy=strategy.method,
                        success=False,
                        error=str(exc),
                        duration_ms=duration_ms,
                    )
                )
                continue

            if element is not None:
                logger.info(
                    "Step %d resolved via %s (score=%s)",
                    action.step_number,
                    strategy.method.value,
                    f"{score:.4f}" if score is not None else "N/A",
                )
                trace.append(
                    ExecutionStepResult.ResolutionAttempt(
                        strategy=strategy.method,
                        success=True,
                        score=score,
                        duration_ms=duration_ms,
                    )
                )
                return element, strategy.method, score, trace

            logger.debug("Strategy %s found no element", strategy.method.value)
            trace.append(
                ExecutionStepResult.ResolutionAttempt(
                    strategy=strategy.method,
                    success=False,
                    duration_ms=duration_ms,
                )
            )

        logger.warning("All strategies exhausted for step %d", action.step_number)
        return None, ResolutionMethod.NONE, None, trace

    async def resolve_element(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, ResolutionMethod, float | None]:
        """Try each strategy in sequence.

        Returns:
            (element_handle, resolution_method, similarity_score)
            If all fail: (None, ResolutionMethod.NONE, None)
        """
        element, method, score, _trace = await self.resolve_element_with_trace(page, action)
        return element, method, score
