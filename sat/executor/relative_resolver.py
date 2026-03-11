"""Relative element resolver — two-phase resolution for relative commands.

When a CNL step includes a relative clause (e.g. ``Click "Submit" Button
below "Email" Label;``), the resolver:

1. **Resolves the anchor element** via the standard StrategyChain.
2. **Filters / re-ranks target candidates** by directional proximity:
   - ``above`` / ``below`` — bounding-box geometry (visual position).
   - ``following`` / ``preceding`` — DOM document order
     (``Node.compareDocumentPosition``).
"""

from __future__ import annotations

import logging
from typing import TypedDict

from playwright.async_api import ElementHandle, Page

from sat.cnl.models import RelativeDirection

logger = logging.getLogger(__name__)


# ── Data types ──────────────────────────────────────────────────────────────

class BoundingBox(TypedDict):
    x: float
    y: float
    width: float
    height: float


class CandidateWithRect(TypedDict):
    index: int
    element: ElementHandle
    rect: BoundingBox


# ── JS helpers ──────────────────────────────────────────────────────────────

# Compare DOM order of two elements.
# Returns: 'following' | 'preceding' | 'same' | 'unknown'
_COMPARE_POSITION_JS = """
(args) => {
    const [anchor, target] = args;
    if (anchor === target) return 'same';
    const pos = anchor.compareDocumentPosition(target);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return 'following';
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 'preceding';
    return 'unknown';
}
"""

# Get bounding box of an element via getBoundingClientRect()
_GET_RECT_JS = """
(el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
}
"""


# ── Spatial filter functions ────────────────────────────────────────────────

def _center_y(r: BoundingBox) -> float:
    """Vertical centre of a bounding box."""
    return r["y"] + r["height"] / 2.0


def _center_x(r: BoundingBox) -> float:
    """Horizontal centre of a bounding box."""
    return r["x"] + r["width"] / 2.0


def filter_by_visual_direction(
    candidates: list[CandidateWithRect],
    anchor_rect: BoundingBox,
    direction: RelativeDirection,
) -> list[CandidateWithRect]:
    """Filter and sort candidates by visual direction relative to anchor.

    For BELOW: keep candidates whose centre-Y is below the anchor's centre-Y,
    sorted by ascending vertical distance.

    For ABOVE: keep candidates whose centre-Y is above the anchor's centre-Y,
    sorted by ascending vertical distance.
    """
    anchor_cy = _center_y(anchor_rect)
    result: list[tuple[float, CandidateWithRect]] = []

    for c in candidates:
        c_cy = _center_y(c["rect"])

        if direction == RelativeDirection.BELOW:
            # Candidate must be visually below the anchor
            if c_cy > anchor_cy:
                dist = c_cy - anchor_cy
                result.append((dist, c))
        elif direction == RelativeDirection.ABOVE:
            # Candidate must be visually above the anchor
            if c_cy < anchor_cy:
                dist = anchor_cy - c_cy
                result.append((dist, c))

    # Sort by distance (closest first)
    result.sort(key=lambda t: t[0])
    return [c for _, c in result]


async def filter_by_dom_order(
    page: Page,
    candidates: list[ElementHandle],
    anchor: ElementHandle,
    direction: RelativeDirection,
) -> list[ElementHandle]:
    """Filter candidates by DOM document order relative to anchor.

    For FOLLOWING: keep candidates that appear *after* anchor in document order.
    For PRECEDING: keep candidates that appear *before* anchor in document order.

    DOM order is determined via ``Node.compareDocumentPosition``.
    """
    result: list[ElementHandle] = []
    target_rel = "following" if direction == RelativeDirection.FOLLOWING else "preceding"

    for candidate in candidates:
        try:
            position = await page.evaluate(
                _COMPARE_POSITION_JS, [anchor, candidate]
            )
            if position == target_rel:
                result.append(candidate)
        except Exception as exc:
            logger.debug("compareDocumentPosition failed: %s", exc)
            continue

    return result


async def get_bounding_box(
    page: Page, element: ElementHandle,
) -> BoundingBox | None:
    """Get the bounding box of an element.

    Uses Playwright's built-in method first, falls back to JS evaluation.
    """
    try:
        box = await element.bounding_box()
        if box:
            return BoundingBox(
                x=box["x"], y=box["y"],
                width=box["width"], height=box["height"],
            )
    except Exception:
        pass

    # Fallback: JS evaluation
    try:
        rect = await page.evaluate(_GET_RECT_JS, element)
        if rect and rect.get("width", 0) > 0:
            return BoundingBox(
                x=rect["x"], y=rect["y"],
                width=rect["width"], height=rect["height"],
            )
    except Exception:
        pass

    return None


def is_visual_direction(direction: RelativeDirection) -> bool:
    """Return True if the direction is visual (above/below)."""
    return direction in (RelativeDirection.ABOVE, RelativeDirection.BELOW)


def is_dom_direction(direction: RelativeDirection) -> bool:
    """Return True if the direction is DOM-order (following/preceding)."""
    return direction in (RelativeDirection.FOLLOWING, RelativeDirection.PRECEDING)
