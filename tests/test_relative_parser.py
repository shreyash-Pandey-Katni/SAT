"""Tests for relative command parsing.

Verifies that the CNL parser correctly extracts relative_direction,
anchor_query, and anchor_type_hint from all supported action types with
all four direction keywords (above, below, following, preceding).
"""

from __future__ import annotations

import pytest

from sat.cnl.models import ConditionType, RelativeDirection
from sat.cnl.parser import parse_cnl
from sat.core.models import ActionType


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _step(cnl: str):
    """Parse a single CNL line and return the first step."""
    result = parse_cnl(cnl)
    assert result.is_valid, f"Parse failed: {result.errors}"
    assert len(result.steps) == 1, f"Expected 1 step, got {len(result.steps)}"
    return result.steps[0]


# ═══════════════════════════════════════════════════════════════════════════
# 1. Click — all 4 directions
# ═══════════════════════════════════════════════════════════════════════════

class TestClickRelative:

    def test_click_below(self):
        s = _step('Click "Add to cart" Button below "Product A" Text;')
        assert s.action_type == ActionType.CLICK
        assert s.element_query == "Add to cart Button"
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.anchor_query == "Product A Text"
        assert s.anchor_type_hint == "Text"

    def test_click_above(self):
        s = _step('Click "Save" Button above "Cancel" Button;')
        assert s.relative_direction == RelativeDirection.ABOVE
        assert s.anchor_query == "Cancel Button"
        assert s.anchor_type_hint == "Button"

    def test_click_following(self):
        s = _step('Click "Edit" Link following "Username" TextField;')
        assert s.relative_direction == RelativeDirection.FOLLOWING
        assert s.anchor_query == "Username Textfield"
        assert s.anchor_type_hint == "Textfield"

    def test_click_preceding(self):
        s = _step('Click "Delete" Button preceding "Confirm" Button;')
        assert s.relative_direction == RelativeDirection.PRECEDING
        assert s.anchor_query == "Confirm Button"

    def test_click_no_relative(self):
        s = _step('Click "Submit" Button;')
        assert s.relative_direction is None
        assert s.anchor_query is None
        assert s.anchor_type_hint is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Type — all 4 directions
# ═══════════════════════════════════════════════════════════════════════════

class TestTypeRelative:

    def test_type_below(self):
        s = _step('Type "hello" in "Password" TextField below "Username" TextField;')
        assert s.action_type == ActionType.TYPE
        assert s.value == "hello"
        assert s.element_query == "Password Textfield"
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.anchor_query == "Username Textfield"

    def test_type_above(self):
        s = _step('Type "world" in "First Name" TextField above "Last Name" TextField;')
        assert s.relative_direction == RelativeDirection.ABOVE
        assert s.anchor_query == "Last Name Textfield"

    def test_type_following(self):
        s = _step('Type "abc" in "Email" TextField following "Name" TextField;')
        assert s.relative_direction == RelativeDirection.FOLLOWING

    def test_type_no_relative(self):
        s = _step('Type "test" in "Search" TextField;')
        assert s.relative_direction is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. Select — relative
# ═══════════════════════════════════════════════════════════════════════════

class TestSelectRelative:

    def test_select_below(self):
        s = _step('Select "Option A" in "Country" Dropdown below "City" Dropdown;')
        assert s.action_type == ActionType.SELECT
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.anchor_query == "City Dropdown"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Check / Uncheck — relative
# ═══════════════════════════════════════════════════════════════════════════

class TestCheckRelative:

    def test_check_below(self):
        s = _step('Check "Agree" Checkbox below "Terms" Text;')
        assert s.action_type == ActionType.CHECK
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.anchor_query == "Terms Text"

    def test_uncheck_preceding(self):
        s = _step('Uncheck "Opt-in" Checkbox preceding "Submit" Button;')
        assert s.action_type == ActionType.UNCHECK
        assert s.relative_direction == RelativeDirection.PRECEDING


# ═══════════════════════════════════════════════════════════════════════════
# 5. Hover — relative
# ═══════════════════════════════════════════════════════════════════════════

class TestHoverRelative:

    def test_hover_below(self):
        s = _step('Hover "$29.99" Text below "Backpack" Text;')
        assert s.action_type == ActionType.HOVER
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.anchor_query == "Backpack Text"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Store — relative
# ═══════════════════════════════════════════════════════════════════════════

class TestStoreRelative:

    def test_store_text_below(self):
        s = _step('Store text of "$29.99" Text below "Backpack" Text as "price";')
        assert s.action_type == ActionType.STORE
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.anchor_query == "Backpack Text"
        assert s.variable_name == "price"

    def test_store_value_following(self):
        s = _step('Store value of "Email" TextField following "Name" TextField as "email_val";')
        assert s.relative_direction == RelativeDirection.FOLLOWING
        assert s.anchor_query == "Name Textfield"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Assert — relative
# ═══════════════════════════════════════════════════════════════════════════

class TestAssertRelative:

    def test_assert_visible_below(self):
        s = _step('Assert "Remove" Button below "Backpack" Text is visible;')
        assert s.action_type == ActionType.ASSERT
        assert s.assertion_type == ConditionType.IS_VISIBLE
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.anchor_query == "Backpack Text"

    def test_assert_hidden_above(self):
        s = _step('Assert "Error" Text above "Submit" Button is hidden;')
        assert s.assertion_type == ConditionType.IS_HIDDEN
        assert s.relative_direction == RelativeDirection.ABOVE

    def test_assert_text_contains_below(self):
        s = _step('Assert text of "Price" Text below "Product" Text contains "29.99";')
        assert s.assertion_type == ConditionType.CONTAINS_TEXT
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.assertion_expected == "29.99"

    def test_assert_text_equals_following(self):
        s = _step('Assert text of "Total" Text following "Cart" Text isEqual "100";')
        assert s.assertion_type == ConditionType.IS_EQUAL
        assert s.relative_direction == RelativeDirection.FOLLOWING
        assert s.assertion_expected == "100"

    def test_assert_value_contains_below(self):
        s = _step('Assert value of "Qty" TextField below "Item" Text contains "1";')
        assert s.assertion_type == ConditionType.CONTAINS_TEXT
        assert s.relative_direction == RelativeDirection.BELOW

    def test_assert_no_relative(self):
        s = _step('Assert "Submit" Button is visible;')
        assert s.relative_direction is None


# ═══════════════════════════════════════════════════════════════════════════
# 8. Verify (legacy) — relative
# ═══════════════════════════════════════════════════════════════════════════

class TestVerifyRelative:

    def test_verify_visible_below(self):
        s = _step('Verify "Remove" Button below "Backpack" Text isVisible;')
        assert s.action_type == ActionType.ASSERT
        assert s.assertion_type == ConditionType.IS_VISIBLE
        assert s.relative_direction == RelativeDirection.BELOW

    def test_verify_hidden_preceding(self):
        s = _step('Verify "Alert" Text preceding "Footer" Text isHidden;')
        assert s.relative_direction == RelativeDirection.PRECEDING

    def test_verify_text_contains_below(self):
        s = _step('Verify text of "Price" Label below "Product" Text contains "9.99";')
        assert s.relative_direction == RelativeDirection.BELOW
        assert s.assertion_expected == "9.99"


# ═══════════════════════════════════════════════════════════════════════════
# 9. Direction keyword case-insensitivity (patterns are case-insensitive)
# ═══════════════════════════════════════════════════════════════════════════

class TestDirectionCaseInsensitive:

    def test_uppercase_below(self):
        """Parser uses re.IGNORECASE so direction keywords in any case work."""
        s = _step('Click "X" Button BELOW "Y" Text;')
        assert s.relative_direction == RelativeDirection.BELOW

    def test_mixed_case(self):
        s = _step('Click "X" Button Following "Y" Text;')
        assert s.relative_direction == RelativeDirection.FOLLOWING


# ═══════════════════════════════════════════════════════════════════════════
# 10. Multi-line with relative and non-relative mixed
# ═══════════════════════════════════════════════════════════════════════════

class TestMixedRelative:

    def test_mixed_steps(self):
        cnl = (
            'Click "Login" Button;\n'
            'Type "user" in "Name" TextField;\n'
            'Click "Submit" Button below "Form" Element;\n'
        )
        result = parse_cnl(cnl)
        assert result.is_valid
        assert len(result.steps) == 3
        assert result.steps[0].relative_direction is None
        assert result.steps[1].relative_direction is None
        assert result.steps[2].relative_direction == RelativeDirection.BELOW
        assert result.steps[2].anchor_query == "Form Element"
