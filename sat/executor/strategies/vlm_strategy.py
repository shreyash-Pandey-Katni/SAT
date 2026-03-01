"""Strategy 3 — VLM-based visual element detection via Ollama.

Takes a screenshot of the current page, sends it to a local LLaVA-style model,
parses the returned coordinates, and resolves an ElementHandle via
document.elementFromPoint(x, y).
"""

from __future__ import annotations

import logging

from playwright.async_api import ElementHandle, Page

from sat.config import VLMStrategyConfig
from sat.core.models import RecordedAction, ResolutionMethod, SelectorInfo
from sat.executor.strategies.base import ResolutionStrategy
from sat.services.ollama_vlm import OllamaVLMService

logger = logging.getLogger(__name__)


class VLMStrategy(ResolutionStrategy):
    """Resolves elements by asking an Ollama VLM to identify them visually."""

    method = ResolutionMethod.VLM

    def __init__(
        self,
        config: VLMStrategyConfig,
        vlm_service: OllamaVLMService | None = None,
    ) -> None:
        self._config = config
        self._svc = vlm_service or OllamaVLMService(
            model=config.model,
            base_url=config.ollama_base_url,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    async def resolve(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, float | None]:
        # Capture screenshot (fast — CDP message, no HTTP)
        try:
            screenshot_bytes = await page.screenshot(type="png")
        except Exception as exc:
            logger.error("VLMStrategy: screenshot failed: %s", exc)
            return None, None

        cnl_desc = action.cnl_step or ""
        selector_desc = _selector_to_str(action.selector)

        coords = await self._svc.identify_element(
            screenshot_bytes=screenshot_bytes,
            action_type=action.action_type.value,
            cnl_description=cnl_desc,
            selector_description=selector_desc,
            original_position=action.element_position,
        )

        if coords is None or not coords.found:
            logger.debug("VLMStrategy: element not found by model")
            return None, None

        logger.debug(
            "VLMStrategy: model identified element at (%.0f, %.0f): %s",
            coords.x, coords.y, coords.description,
        )

        # Resolve ElementHandle from coordinates
        element = await self._element_from_point(page, coords.x, coords.y)
        if element is None:
            logger.debug("VLMStrategy: elementFromPoint returned null at (%.0f, %.0f)", coords.x, coords.y)
        return element, None

    # ------------------------------------------------------------------

    @staticmethod
    async def _element_from_point(page: Page, x: float, y: float) -> ElementHandle | None:
        """Get the topmost element at the given viewport coordinates."""
        try:
            handle = await page.evaluate_handle(
                "([x, y]) => document.elementFromPoint(x, y)", [x, y]
            )
            return handle.as_element()
        except Exception as exc:
            logger.warning("elementFromPoint failed: %s", exc)
            return None


def _selector_to_str(s: SelectorInfo | None) -> str:
    if s is None:
        return ""
    parts = []
    if s.tag_name: parts.append(f"tag={s.tag_name}")
    if s.text_content: parts.append(f"text='{s.text_content}'")
    if s.aria_label: parts.append(f"aria-label='{s.aria_label}'")
    if s.placeholder: parts.append(f"placeholder='{s.placeholder}'")
    if s.id: parts.append(f"id='{s.id}'")
    if s.class_name: parts.append(f"class='{s.class_name}'")
    return ", ".join(parts)
