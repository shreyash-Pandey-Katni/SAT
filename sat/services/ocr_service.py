"""OCRService — OCR extraction and fuzzy text matching for fallback resolution."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class OCRTextRegion:
    """A single OCR text region with geometry and confidence."""

    text: str
    bbox: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]
    confidence: float
    center_x: float
    center_y: float


class OCRService:
    """Extracts text regions from screenshots and fuzzy-matches labels."""

    def __init__(
        self,
        languages: list[str] | None = None,
        gpu: bool = False,
        min_confidence: float = 0.80,
    ) -> None:
        self._languages = languages or ["en"]
        self._gpu = gpu
        self._min_confidence = min_confidence
        self._reader: Any | None = None
        self._unavailable = False

    async def extract_text_regions(self, screenshot_bytes: bytes) -> list[OCRTextRegion]:
        """Run OCR over screenshot bytes and return text regions above confidence threshold."""
        if not screenshot_bytes:
            return []
        if not await self._ensure_reader():
            return []

        return await asyncio.to_thread(self._extract_sync, screenshot_bytes)

    def match_label(
        self,
        label: str,
        regions: list[OCRTextRegion],
        min_match_score: float,
    ) -> tuple[OCRTextRegion | None, float | None]:
        """Return best OCR region for label if fuzzy score passes threshold."""
        return match_label(label=label, regions=regions, min_match_score=min_match_score)

    async def _ensure_reader(self) -> bool:
        if self._reader is not None:
            return True
        if self._unavailable:
            return False

        try:
            import easyocr  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "OCRService: easyocr is not installed. Install optional deps with 'pip install sat[ocr]'."
            )
            self._unavailable = True
            return False

        def _build_reader() -> Any:
            return easyocr.Reader(self._languages, gpu=self._gpu)

        self._reader = await asyncio.to_thread(_build_reader)
        return True

    def _extract_sync(self, screenshot_bytes: bytes) -> list[OCRTextRegion]:
        assert self._reader is not None

        image = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
        image_np = np.array(image)
        results = self._reader.readtext(image_np)

        regions: list[OCRTextRegion] = []
        for result in results:
            if len(result) < 3:
                continue
            raw_bbox, raw_text, raw_conf = result[0], result[1], result[2]
            text = str(raw_text or "").strip()
            confidence = float(raw_conf or 0.0)
            bbox = _normalize_bbox(raw_bbox)
            if not text or confidence < self._min_confidence or bbox is None:
                continue
            center_x, center_y = _center_of_bbox(bbox)
            regions.append(
                OCRTextRegion(
                    text=text,
                    bbox=bbox,
                    confidence=confidence,
                    center_x=center_x,
                    center_y=center_y,
                )
            )

        return regions


def match_label(
    label: str,
    regions: list[OCRTextRegion],
    min_match_score: float,
) -> tuple[OCRTextRegion | None, float | None]:
    """Pick the highest scoring OCR region for *label* if score passes threshold."""
    clean_label = _normalize_text(label)
    if not clean_label or not regions:
        return None, None

    best_region: OCRTextRegion | None = None
    best_score = 0.0

    for region in regions:
        score = _score_text_match(clean_label, _normalize_text(region.text))
        if score > best_score:
            best_region = region
            best_score = score

    if best_region is None or best_score < min_match_score:
        return None, None
    return best_region, best_score


def _score_text_match(label: str, candidate: str) -> float:
    if not label or not candidate:
        return 0.0

    # Fast path for exact matches.
    if label == candidate:
        return 1.0

    try:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]

        score = max(
            fuzz.ratio(label, candidate),
            fuzz.partial_ratio(label, candidate),
            fuzz.token_sort_ratio(label, candidate),
        )
        return float(score / 100.0)
    except ImportError:
        from difflib import SequenceMatcher

        return float(SequenceMatcher(None, label, candidate).ratio())


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _center_of_bbox(
    bbox: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]],
) -> tuple[float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return sum(xs) / 4.0, sum(ys) / 4.0


def _normalize_bbox(raw_bbox: Any) -> (
    tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None
):
    try:
        points = tuple((float(p[0]), float(p[1])) for p in raw_bbox)
    except (TypeError, ValueError, IndexError):
        return None

    if len(points) != 4:
        return None
    return points
