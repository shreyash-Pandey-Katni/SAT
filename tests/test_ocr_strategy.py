"""Unit tests for OCR fallback strategy and OCR text matching."""

from __future__ import annotations

import asyncio

import pytest

from sat.config import OCRStrategyConfig
from sat.core.models import ActionType, RecordedAction, SelectorInfo
from sat.executor.strategies.ocr_strategy import OCRStrategy, _extract_label
from sat.services.ocr_service import OCRTextRegion, match_label


class _FakeHandle:
    def __init__(self, element: object | None) -> None:
        self._element = element

    def as_element(self) -> object | None:
        return self._element


class _FakePage:
    def __init__(self, element: object | None = object()) -> None:
        self._element = element
        self.last_point: tuple[float, float] | None = None
        self.screenshot_calls = 0

    async def screenshot(self, type: str = "png") -> bytes:
        assert type == "png"
        self.screenshot_calls += 1
        return b"fake-image"

    async def evaluate_handle(self, _script: str, args: list[float]) -> _FakeHandle:
        self.last_point = (float(args[0]), float(args[1]))
        return _FakeHandle(self._element)


class _FakeOCRService:
    def __init__(self, regions: list[OCRTextRegion], matched: tuple[OCRTextRegion | None, float | None]):
        self._regions = regions
        self._matched = matched

    async def extract_text_regions(self, _screenshot_bytes: bytes) -> list[OCRTextRegion]:
        return self._regions

    def match_label(
        self,
        label: str,
        regions: list[OCRTextRegion],
        min_match_score: float,
    ) -> tuple[OCRTextRegion | None, float | None]:
        assert label
        assert regions is self._regions
        assert min_match_score > 0
        return self._matched


def _make_action(selector: SelectorInfo | None = None, cnl_step: str | None = None) -> RecordedAction:
    return RecordedAction(
        step_number=1,
        action_type=ActionType.CLICK,
        url="https://example.com",
        tab_id="tab-1",
        selector=selector,
        cnl_step=cnl_step,
    )


def _region(text: str, x: float, y: float, confidence: float = 0.99) -> OCRTextRegion:
    return OCRTextRegion(
        text=text,
        bbox=((x - 10, y - 5), (x + 10, y - 5), (x + 10, y + 5), (x - 10, y + 5)),
        confidence=confidence,
        center_x=x,
        center_y=y,
    )


def test_match_label_exact_hit():
    regions = [_region("cancel", 20, 20), _region("submit", 100, 200)]
    region, score = match_label("Submit", regions, min_match_score=0.85)
    assert region is not None
    assert region.text == "submit"
    assert score is not None
    assert score >= 0.85


def test_match_label_below_threshold_returns_none():
    regions = [_region("cancel", 10, 10)]
    region, score = match_label("submit", regions, min_match_score=0.90)
    assert region is None
    assert score is None


def test_extract_label_prefers_placeholder_then_text_then_aria_then_cnl():
    from_placeholder = _make_action(
        selector=SelectorInfo(tag_name="input", placeholder="Email", text_content="Ignored"),
        cnl_step='Type "a" in "User" TextField;',
    )
    assert _extract_label(from_placeholder) == "Email"

    from_text = _make_action(
        selector=SelectorInfo(tag_name="button", text_content="Sign in", aria_label="Login"),
        cnl_step='Click "Login" Button;',
    )
    assert _extract_label(from_text) == "Sign in"

    from_cnl = _make_action(selector=SelectorInfo(tag_name="div"), cnl_step='Click "Open" Button;')
    assert _extract_label(from_cnl) == 'Click "Open" Button;'


def test_ocr_strategy_resolves_element_from_matched_region():
    matched_region = _region("Sign in", 120, 260)
    fake_service = _FakeOCRService(
        regions=[matched_region],
        matched=(matched_region, 0.96),
    )
    strategy = OCRStrategy(
        config=OCRStrategyConfig(min_confidence=0.5, min_match_score=0.85),
        ocr_service=fake_service,  # type: ignore[arg-type]
    )

    page = _FakePage(element=object())
    action = _make_action(selector=SelectorInfo(tag_name="button", text_content="Sign in"))

    element, score = asyncio.run(strategy.resolve(page, action))
    assert element is not None
    assert score == pytest.approx(0.96)
    assert page.last_point == pytest.approx((120.0, 260.0))


def test_ocr_strategy_returns_none_without_label_or_match():
    fake_service = _FakeOCRService(
        regions=[_region("something", 33, 44)],
        matched=(None, None),
    )
    strategy = OCRStrategy(
        config=OCRStrategyConfig(min_confidence=0.5, min_match_score=0.85),
        ocr_service=fake_service,  # type: ignore[arg-type]
    )

    page = _FakePage()

    no_label_action = _make_action(selector=SelectorInfo(tag_name="button"), cnl_step=None)
    element, score = asyncio.run(strategy.resolve(page, no_label_action))
    assert element is None
    assert score is None
    assert page.screenshot_calls == 0

    unmatched_action = _make_action(selector=SelectorInfo(tag_name="button", text_content="Missing"))
    element, score = asyncio.run(strategy.resolve(page, unmatched_action))
    assert element is None
    assert score is None
    assert page.screenshot_calls == 1
