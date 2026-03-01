"""StrategyChain — runs resolution strategies in priority order until one succeeds."""

from __future__ import annotations

import logging

from playwright.async_api import ElementHandle, Page

from sat.core.models import RecordedAction, ResolutionMethod
from sat.executor.strategies.base import ResolutionStrategy

logger = logging.getLogger(__name__)


class StrategyChain:
    """Executes strategies in order, returning the first successful result."""

    def __init__(self, strategies: list[ResolutionStrategy]) -> None:
        self._strategies = strategies

    async def resolve_element(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, ResolutionMethod, float | None]:
        """Try each strategy in sequence.

        Returns:
            (element_handle, resolution_method, similarity_score)
            If all fail: (None, ResolutionMethod.NONE, None)
        """
        for strategy in self._strategies:
            logger.debug("Trying strategy: %s", strategy.method.value)
            try:
                element, score = await strategy.resolve(page, action)
            except Exception as exc:
                logger.error(
                    "Strategy %s raised an exception: %s", strategy.method.value, exc
                )
                continue

            if element is not None:
                logger.info(
                    "Step %d resolved via %s (score=%s)",
                    action.step_number,
                    strategy.method.value,
                    f"{score:.4f}" if score is not None else "N/A",
                )
                return element, strategy.method, score

            logger.debug("Strategy %s found no element", strategy.method.value)

        logger.warning("All strategies exhausted for step %d", action.step_number)
        return None, ResolutionMethod.NONE, None
