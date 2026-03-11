"""Tests for relative_resolver utility functions.

Unit tests for the spatial/DOM-order filter helpers that don't require
a live browser.  The Playwright-dependent functions are tested via
integration tests.
"""

from __future__ import annotations

import pytest

from sat.cnl.models import RelativeDirection
from sat.executor.relative_resolver import (
    filter_by_visual_direction,
    is_visual_direction,
    is_dom_direction,
    BoundingBox,
    CandidateWithRect,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Direction predicates
# ═══════════════════════════════════════════════════════════════════════════

class TestDirectionPredicates:

    def test_above_is_visual(self):
        assert is_visual_direction(RelativeDirection.ABOVE) is True

    def test_below_is_visual(self):
        assert is_visual_direction(RelativeDirection.BELOW) is True

    def test_following_is_dom(self):
        assert is_dom_direction(RelativeDirection.FOLLOWING) is True

    def test_preceding_is_dom(self):
        assert is_dom_direction(RelativeDirection.PRECEDING) is True

    def test_following_not_visual(self):
        assert is_visual_direction(RelativeDirection.FOLLOWING) is False

    def test_above_not_dom(self):
        assert is_dom_direction(RelativeDirection.ABOVE) is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. Visual direction filtering
# ═══════════════════════════════════════════════════════════════════════════

def _make_cand(index: int, x: float, y: float, w: float = 50, h: float = 20) -> CandidateWithRect:
    """Create a CandidateWithRect with a dummy element (None for unit tests)."""
    return CandidateWithRect(
        index=index,
        element=None,  # type: ignore[arg-type]
        rect=BoundingBox(x=x, y=y, width=w, height=h),
    )


class TestFilterByVisualDirection:

    def test_below_filters_candidates_under_anchor(self):
        # Anchor at y=100, h=20 → center_y=110
        anchor = BoundingBox(x=50, y=100, width=200, height=20)
        candidates = [
            _make_cand(0, 50, 50),    # above: center_y=60
            _make_cand(1, 50, 130),   # below: center_y=140
            _make_cand(2, 50, 200),   # below: center_y=210
            _make_cand(3, 50, 100),   # same: center_y=110
        ]
        result = filter_by_visual_direction(candidates, anchor, RelativeDirection.BELOW)
        indices = [c["index"] for c in result]
        assert 1 in indices
        assert 2 in indices
        assert 0 not in indices

    def test_above_filters_candidates_over_anchor(self):
        anchor = BoundingBox(x=50, y=200, width=200, height=20)
        candidates = [
            _make_cand(0, 50, 50),    # above: center_y=60
            _make_cand(1, 50, 130),   # above: center_y=140
            _make_cand(2, 50, 300),   # below: center_y=310
        ]
        result = filter_by_visual_direction(candidates, anchor, RelativeDirection.ABOVE)
        indices = [c["index"] for c in result]
        assert 0 in indices
        assert 1 in indices
        assert 2 not in indices

    def test_below_sorted_by_distance(self):
        anchor = BoundingBox(x=50, y=100, width=200, height=20)
        candidates = [
            _make_cand(0, 50, 300),   # far below
            _make_cand(1, 50, 130),   # closer below
            _make_cand(2, 50, 200),   # mid below
        ]
        result = filter_by_visual_direction(candidates, anchor, RelativeDirection.BELOW)
        indices = [c["index"] for c in result]
        # Should be sorted closest-first
        assert indices[0] == 1
        assert indices[1] == 2
        assert indices[2] == 0

    def test_empty_candidates(self):
        anchor = BoundingBox(x=50, y=100, width=200, height=20)
        result = filter_by_visual_direction([], anchor, RelativeDirection.BELOW)
        assert result == []

    def test_no_matches(self):
        anchor = BoundingBox(x=50, y=500, width=200, height=20)
        candidates = [
            _make_cand(0, 50, 50),    # above
            _make_cand(1, 50, 100),   # above
        ]
        result = filter_by_visual_direction(candidates, anchor, RelativeDirection.BELOW)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. Text scoring helper (CNLRunner._text_score_candidate)
# ═══════════════════════════════════════════════════════════════════════════

class TestTextScoring:
    """Test the static text scoring used in relative resolution."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from sat.executor.cnl_runner import CNLRunner
        self.score = CNLRunner._text_score_candidate

    def test_exact_match(self):
        cand = {"text": "Submit", "placeholder": "", "ariaLabel": "", "name": ""}
        assert self.score("Submit", cand) == 1.0

    def test_case_insensitive_match(self):
        cand = {"text": "submit", "placeholder": "", "ariaLabel": "", "name": ""}
        assert self.score("Submit", cand) == 1.0

    def test_partial_match(self):
        cand = {"text": "Add to cart", "placeholder": "", "ariaLabel": "", "name": ""}
        score = self.score("Add to", cand)
        assert 0.5 < score < 1.0

    def test_no_match(self):
        cand = {"text": "Cancel", "placeholder": "", "ariaLabel": "", "name": ""}
        assert self.score("Submit", cand) == 0.0

    def test_placeholder_match(self):
        cand = {"text": "", "placeholder": "Enter email", "ariaLabel": "", "name": ""}
        assert self.score("Enter email", cand) == 1.0

    def test_empty_label(self):
        cand = {"text": "Something", "placeholder": "", "ariaLabel": "", "name": ""}
        assert self.score("", cand) == 0.0
