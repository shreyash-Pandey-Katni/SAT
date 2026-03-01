"""OllamaVLMService — vision-language model via Ollama (llava:13b, etc.).

Used as the last-resort fallback in the executor strategy chain.
Sends a screenshot with a natural-language prompt and parses the returned
bounding-box / coordinate information.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass

import ollama

logger = logging.getLogger(__name__)


@dataclass
class VLMCoordinates:
    x: float
    y: float
    description: str = ""
    found: bool = True


class OllamaVLMService:
    """Wraps Ollama's multimodal chat endpoint for coordinate-based element detection."""

    def __init__(
        self,
        model: str = "llava:13b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> None:
        self._model = model
        self._client = ollama.AsyncClient(host=base_url)
        self._temperature = temperature
        self._max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def identify_element(
        self,
        screenshot_bytes: bytes,
        action_type: str,
        cnl_description: str,
        selector_description: str,
        original_position: dict | None = None,
    ) -> VLMCoordinates | None:
        """Ask the VLM to locate an element on the screenshot.

        Returns :class:`VLMCoordinates` on success, or ``None`` if the model
        says the element was not found or returns an unparseable response.
        """
        prompt = self._build_prompt(
            action_type, cnl_description, selector_description, original_position
        )
        image_b64 = base64.b64encode(screenshot_bytes).decode()

        try:
            response = await self._client.chat(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [image_b64],
                    }
                ],
                options={
                    "temperature": self._temperature,
                    "num_predict": self._max_tokens,
                },
            )
        except Exception as exc:
            logger.error("VLM call failed: %s", exc)
            return None

        content = response["message"]["content"]
        logger.debug("VLM response: %s", content[:300])
        return self._parse_response(content)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        action_type: str,
        cnl_description: str,
        selector_description: str,
        original_position: dict | None,
    ) -> str:
        pos_hint = ""
        if original_position:
            pos_hint = (
                f"\nThe element was originally near position "
                f"(x={original_position.get('x', '?')}, "
                f"y={original_position.get('y', '?')})."
            )

        return f"""I need to locate a UI element in this browser screenshot to perform a '{action_type}' action.

CNL description: {cnl_description or 'N/A'}
Original element attributes: {selector_description or 'N/A'}{pos_hint}

Instructions:
1. Look carefully at the screenshot.
2. Find the element that best matches the description above.
3. Return ONLY a JSON object on a single line with these exact fields:
   {{"found": true, "x": <center_x>, "y": <center_y>, "description": "<brief description of what you found>"}}
   OR if the element cannot be found:
   {{"found": false, "description": "<why not found>"}}

Do not include any other text — only the JSON object."""

    def _parse_response(self, content: str) -> VLMCoordinates | None:
        """Extract JSON coordinate data from the VLM response."""
        # Try direct JSON parse first
        content = content.strip()
        # Find JSON object in the response (the model may add surrounding text)
        json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if not json_match:
            logger.warning("VLM returned no JSON object: %s", content[:200])
            return None

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            logger.warning("VLM JSON parse failed: %s", content[:200])
            return None

        if not data.get("found", False):
            logger.info("VLM reported element not found: %s", data.get("description"))
            return None

        x = data.get("x")
        y = data.get("y")
        if x is None or y is None:
            logger.warning("VLM JSON missing x/y: %s", data)
            return None

        return VLMCoordinates(
            x=float(x),
            y=float(y),
            description=str(data.get("description", "")),
            found=True,
        )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True if Ollama VLM model is available."""
        try:
            models_response = await self._client.list()
            available = [m["name"] for m in models_response.get("models", [])]
            if not any(self._model.split(":")[0] in m for m in available):
                logger.warning(
                    "VLM model %r not found in Ollama. Available: %s",
                    self._model, available
                )
                return False
            return True
        except Exception as exc:
            logger.warning("VLM health check failed: %s", exc)
            return False
