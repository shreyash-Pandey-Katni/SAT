"""Strategy 3 — OCR-based visual text matching.

Takes a screenshot, extracts OCR text regions, fuzzy-matches the recorded label,
and resolves an ElementHandle via document.elementFromPoint(x, y).
"""

from __future__ import annotations

import logging

from playwright.async_api import ElementHandle, Page

from sat.config import OCRStrategyConfig
from sat.core.models import RecordedAction, ResolutionMethod
from sat.executor.strategies.base import ResolutionStrategy
from sat.services.ocr_service import OCRService

logger = logging.getLogger(__name__)


class OCRStrategy(ResolutionStrategy):
    """Resolves elements by OCR text matching on a viewport screenshot."""

    method = ResolutionMethod.OCR

    def __init__(
        self,
        config: OCRStrategyConfig,
        ocr_service: OCRService | None = None,
    ) -> None:
        self._config = config
        self._ocr = ocr_service or OCRService(
            languages=config.languages,
            gpu=config.gpu,
            min_confidence=config.min_confidence,
        )

    async def resolve(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, float | None]:
        label = _extract_label(action)
        if not label:
            logger.debug("OCRStrategy: no label available for step %d", action.step_number)
            return None, None

        try:
            screenshot_bytes = await page.screenshot(type="png")
        except Exception as exc:
            logger.error("OCRStrategy: screenshot failed: %s", exc)
            return None, None

        regions = await self._ocr.extract_text_regions(screenshot_bytes)
        if not regions:
            logger.debug("OCRStrategy: no OCR regions found")
            return None, None

        region, score = self._ocr.match_label(
            label=label,
            regions=regions,
            min_match_score=self._config.min_match_score,
        )
        if region is None or score is None:
            logger.debug("OCRStrategy: no match above threshold for label=%r", label)
            return None, None

        element = await self._element_from_point(page, region.center_x, region.center_y)
        if element is None:
            logger.debug(
                "OCRStrategy: elementFromPoint returned null at (%.1f, %.1f)",
                region.center_x,
                region.center_y,
            )
            return None, None

        logger.debug(
            "OCRStrategy: matched %r with score=%.3f at (%.1f, %.1f)",
            region.text,
            score,
            region.center_x,
            region.center_y,
        )
        return element, score

    @staticmethod
    async def _element_from_point(page: Page, x: float, y: float) -> ElementHandle | None:
        try:
            handle = await page.evaluate_handle(
                "([x, y]) => document.elementFromPoint(x, y)", [x, y]
            )
            return handle.as_element()
        except Exception as exc:
            logger.warning("OCRStrategy: elementFromPoint failed: %s", exc)
            return None


def _extract_label(action: RecordedAction) -> str | None:
    """Return best human-readable label for OCR matching."""
    s = action.selector
    if s:
        if s.placeholder:
            return s.placeholder.strip()
        if s.text_content:
            return s.text_content.strip()
        if s.aria_label:
            return s.aria_label.strip()
    if action.cnl_step:
        return action.cnl_step.strip()
    return None
