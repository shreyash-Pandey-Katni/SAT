"""Tests for old CNL → SAT 2.0 parser compatibility.

Covers: Verify (assertions), IfSatisfy (conditions), Check/Uncheck,
Select modes, and old control-type mapping.
"""

from __future__ import annotations

from sat.cnl.models import ConditionType
from sat.cnl.parser import parse_cnl, _normalise_type, _resolve_type
from sat.core.models import ActionType


# ═══════════════════════════════════════════════════════════════════════════
# 1. Old control-type mapping
# ═══════════════════════════════════════════════════════════════════════════


class TestControlTypeMapping:

    def test_combobox_maps_to_dropdown(self):
        assert _resolve_type("ComboBox") == "dropdown"
        assert _normalise_type("ComboBox") == "Dropdown"

    def test_radiobutton_maps_to_radio(self):
        assert _resolve_type("RadioButton") == "radio"
        assert _normalise_type("RadioButton") == "Radio"

    def test_textarea_maps_to_textfield(self):
        assert _normalise_type("TextArea") == "Textfield"

    def test_label_maps_to_text(self):
        assert _normalise_type("Label") == "Text"

    def test_div_maps_to_element(self):
        assert _normalise_type("Div") == "Element"

    def test_known_types_unchanged(self):
        assert _normalise_type("Button") == "Button"
        assert _normalise_type("Link") == "Link"
        assert _normalise_type("TextField") == "Textfield"
        assert _normalise_type("Checkbox") == "Checkbox"

    def test_unknown_type_returns_none(self):
        assert _normalise_type("FooBar") is None
        assert _normalise_type(None) is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Verify patterns (old CNL → ASSERT)
# ═══════════════════════════════════════════════════════════════════════════


class TestVerifyPatterns:

    def test_verify_quoted_isvisible(self):
        result = parse_cnl('Verify "Dashboard" Label isVisible;')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.ASSERT
        assert step.assertion_type == ConditionType.IS_VISIBLE
        assert "Dashboard" in step.element_query
        assert step.element_type_hint == "Text"  # Label → Text

    def test_verify_quoted_ispresent(self):
        result = parse_cnl('Verify "Log Off " Link isPresent;')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.ASSERT
        assert step.assertion_type == ConditionType.IS_VISIBLE

    def test_verify_quoted_ishidden(self):
        result = parse_cnl('Verify "Error" Label isHidden;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_HIDDEN

    def test_verify_quoted_isnotvisible(self):
        result = parse_cnl('Verify "Warning" Label isNotVisible;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_HIDDEN

    def test_verify_quoted_isnotpresent(self):
        result = parse_cnl('Verify "Alert" Button isNotPresent;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_HIDDEN

    def test_verify_isenabled(self):
        result = parse_cnl('Verify "Submit" Button isEnabled;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_ENABLED

    def test_verify_isdisabled(self):
        result = parse_cnl('Verify "Submit" Button isDisabled;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_DISABLED

    def test_verify_isnotenabled(self):
        result = parse_cnl('Verify "Submit" Button isNotEnabled;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_DISABLED

    def test_verify_text_contains(self):
        result = parse_cnl('Verify text of "Status" Label contains "Active";')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.CONTAINS_TEXT
        assert step.assertion_expected == "Active"
        assert step.store_attribute == "text"

    def test_verify_text_isequal(self):
        result = parse_cnl('Verify text of "Name" TextField isEqual "John";')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_EQUAL
        assert step.assertion_expected == "John"

    def test_verify_isequal_variable(self):
        """Verify isEqual <subject> "expected" — variable comparison."""
        result = parse_cnl('Verify isEqual myVar "https";')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.ASSERT
        assert step.assertion_type == ConditionType.IS_EQUAL
        assert step.assertion_expected == "https"
        assert step.element_query == "myVar"

    def test_verify_contains_variable(self):
        result = parse_cnl('Verify contains myVar "http";')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.CONTAINS_TEXT
        assert step.assertion_expected == "http"

    def test_verify_unquoted_label_visible(self):
        result = parse_cnl('Verify Dashboard Label isVisible;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_VISIBLE
        assert "Dashboard" in step.element_query

    def test_verify_unquoted_label_hidden(self):
        result = parse_cnl('Verify Different label isNotPresent;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_HIDDEN

    def test_verify_case_insensitive(self):
        result = parse_cnl('Verify "Dashboard" Label isvisible;')
        assert result.is_valid
        assert result.steps[0].assertion_type == ConditionType.IS_VISIBLE

    def test_verify_no_type(self):
        """Verify with quoted label but no element type."""
        result = parse_cnl('Verify "Dashboard" isVisible;')
        assert result.is_valid
        step = result.steps[0]
        assert step.assertion_type == ConditionType.IS_VISIBLE
        # No type hint since "isVisible" is not a known type
        # The label captured may vary — just check it parsed


# ═══════════════════════════════════════════════════════════════════════════
# 3. Check / Uncheck
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckUncheck:

    def test_check_checkbox(self):
        result = parse_cnl('Check "Remember me" CheckBox;')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.CHECK
        assert "Remember me" in step.element_query
        assert step.element_type_hint == "Checkbox"

    def test_uncheck_checkbox(self):
        result = parse_cnl('Uncheck "Subscribe" CheckBox;')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.UNCHECK
        assert step.element_type_hint == "Checkbox"

    def test_check_no_type(self):
        """Check without explicit type defaults to Checkbox."""
        result = parse_cnl('Check "Accept terms";')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.CHECK
        assert step.element_type_hint == "Checkbox"

    def test_check_radiobutton(self):
        """Old CNL RadioButton type."""
        result = parse_cnl('Check "Option A" RadioButton;')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.CHECK
        assert step.element_type_hint == "Radio"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Select modes
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectModes:

    def test_select_text(self):
        result = parse_cnl('Select "Option A" in "Dropdown" ComboBox;')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.SELECT
        assert step.value == "Option A"
        assert step.select_mode == "text"
        assert step.element_type_hint == "Dropdown"

    def test_select_by_index(self):
        result = parse_cnl('Select "index=2" in "Items" Dropdown;')
        assert result.is_valid
        step = result.steps[0]
        assert step.value == "2"
        assert step.select_mode == "index"

    def test_select_by_value(self):
        result = parse_cnl('Select "value=opt_a" in "Items" Dropdown;')
        assert result.is_valid
        step = result.steps[0]
        assert step.value == "opt_a"
        assert step.select_mode == "value"

    def test_select_plain_text_default(self):
        result = parse_cnl('Select "Active" in "Status" Dropdown;')
        assert result.is_valid
        step = result.steps[0]
        assert step.select_mode == "text"
        assert step.value == "Active"


# ═══════════════════════════════════════════════════════════════════════════
# 5. IfSatisfy block
# ═══════════════════════════════════════════════════════════════════════════


class TestIfSatisfyBlock:

    def test_basic_ifsatisfy_with_execute(self):
        cnl = """\
ifSatisfy
    <Verify "Submit" Button isVisible>
execute
    <Click "Submit" Button>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        assert len(result.conditional_blocks) == 1
        block = result.conditional_blocks[0]
        assert block.condition.condition_type == ConditionType.IS_VISIBLE
        assert "Submit" in block.condition.element_query
        assert len(block.then_steps) == 1
        assert block.then_steps[0].action_type == ActionType.CLICK
        assert len(block.else_steps) == 0

    def test_ifsatisfy_with_else(self):
        cnl = """\
ifSatisfy
    <Verify "Error" Label isHidden>
execute
    <Click "Continue" Button>
else
    <Click "Cancel" Button>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        block = result.conditional_blocks[0]
        assert len(block.then_steps) == 1
        assert len(block.else_steps) == 1
        assert block.else_steps[0].action_type == ActionType.CLICK

    def test_ifsatisfy_with_noop_else(self):
        cnl = """\
ifSatisfy
    <Verify "Advanced" Button isVisible>
execute
    <Click "Advanced" Button>
    <Click "Proceed to" Link>
else
    <>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        block = result.conditional_blocks[0]
        assert len(block.then_steps) == 2
        assert len(block.else_steps) == 0  # <> is a no-op

    def test_ifsatisfy_isequal_condition(self):
        cnl = """\
ifSatisfy
    <Verify isEqual myProtocol "https">
execute
    <Click "Advanced" Button>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        block = result.conditional_blocks[0]
        assert block.condition.condition_type == ConditionType.IS_EQUAL
        assert block.condition.expected_value == "https"
        assert block.condition.element_query == "myProtocol"

    def test_ifsatisfy_contains_condition(self):
        cnl = """\
ifSatisfy
    <Verify contains myUrl "localhost">
execute
    <Click "OK" Button>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        block = result.conditional_blocks[0]
        assert block.condition.condition_type == ConditionType.CONTAINS_TEXT
        assert block.condition.expected_value == "localhost"

    def test_ifsatisfy_text_comparison_condition(self):
        cnl = """\
ifSatisfy
    <Verify text of "Status" Label contains "Active">
execute
    <Click "Continue" Button>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        block = result.conditional_blocks[0]
        assert block.condition.condition_type == ConditionType.CONTAINS_TEXT
        assert block.condition.expected_value == "Active"

    def test_ifsatisfy_ends_at_regular_statement(self):
        """IfSatisfy block ends when a non-<> line is encountered."""
        cnl = """\
ifSatisfy
    <Verify "Submit" Button isVisible>
execute
    <Click "Submit" Button>
Click "Home" Link;
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        assert len(result.conditional_blocks) == 1
        # The "Click Home Link;" should be parsed as a regular step
        regular_steps = [
            s for s in result.steps
            if s not in result.conditional_blocks[0].then_steps
            and s not in result.conditional_blocks[0].else_steps
        ]
        assert len(regular_steps) == 1
        assert regular_steps[0].action_type == ActionType.CLICK
        assert "Home" in regular_steps[0].element_query

    def test_ifsatisfy_unquoted_condition(self):
        cnl = """\
ifSatisfy
    <Verify Different label isNotPresent>
execute
    <Click "OK" Button>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        block = result.conditional_blocks[0]
        assert block.condition.condition_type == ConditionType.IS_HIDDEN

    def test_ifsatisfy_no_condition_gives_error(self):
        cnl = """\
ifSatisfy
execute
    <Click "OK" Button>
"""
        result = parse_cnl(cnl)
        assert not result.is_valid  # should have an error

    def test_ifsatisfy_multiple_execute_steps(self):
        cnl = """\
ifSatisfy
    <Verify "Panel" Label isVisible>
execute
    <Click "Tab 1" Link>
    <Click "Tab 2" Link>
    <Click "Tab 3" Link>
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        block = result.conditional_blocks[0]
        assert len(block.then_steps) == 3


# ═══════════════════════════════════════════════════════════════════════════
# 6. Mixed old + new CNL
# ═══════════════════════════════════════════════════════════════════════════


class TestMixedSyntax:

    def test_old_cnl_full_example(self):
        """Real-world example with old CNL commands."""
        cnl = """\
#CloseAllWebBrowsers;
#LoginToIS user pass host port;
Type "admin" in "Enter username" TextField;
Type "admin" in "Enter password" TextField;
Click "Log in" Button;
Verify "Dashboard" Label isVisible;
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        assert len(result.steps) == 4  # 2 comments, 4 commands
        assert result.steps[0].action_type == ActionType.TYPE
        assert result.steps[1].action_type == ActionType.TYPE
        assert result.steps[2].action_type == ActionType.CLICK
        assert result.steps[3].action_type == ActionType.ASSERT

    def test_ifsatisfy_followed_by_regular_commands(self):
        """IfSatisfy mixed with regular statements."""
        cnl = """\
Click "Login" Button;
ifSatisfy
    <Verify "Error" Label isVisible>
execute
    <Click "Retry" Button>
else
    <>
Verify "Dashboard" Label isVisible;
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        assert len(result.conditional_blocks) == 1
        # Should have: Login click + Retry (in block) + Dashboard verify
        # (block steps are flattened into steps list)
        assert any(
            s.action_type == ActionType.CLICK and "Login" in s.element_query
            for s in result.steps
        )
        assert any(
            s.action_type == ActionType.ASSERT and "Dashboard" in s.element_query
            for s in result.steps
        )


# ═══════════════════════════════════════════════════════════════════════════
# 7. SAT 2.0 existing syntax still works
# ═══════════════════════════════════════════════════════════════════════════


class TestSAT20SyntaxUnchanged:

    def test_click(self):
        result = parse_cnl('Click "Submit" Button;')
        assert result.is_valid
        assert result.steps[0].action_type == ActionType.CLICK

    def test_type(self):
        result = parse_cnl('Type "hello" in "Search" TextField;')
        assert result.is_valid
        assert result.steps[0].action_type == ActionType.TYPE

    def test_navigate(self):
        result = parse_cnl('Navigate to "http://example.com";')
        assert result.is_valid
        assert result.steps[0].action_type == ActionType.NAVIGATE

    def test_goto_url(self):
        result = parse_cnl('GoToURL "http://example.com";')
        assert result.is_valid
        assert result.steps[0].action_type == ActionType.NAVIGATE

    def test_assert_visible(self):
        result = parse_cnl('Assert "Submit" Button is visible;')
        assert result.is_valid
        assert result.steps[0].assertion_type == ConditionType.IS_VISIBLE

    def test_if_else_block(self):
        cnl = """\
If "Submit" Button is visible {
    Click "Submit" Button;
} Else {
    Click "Cancel" Button;
}
"""
        result = parse_cnl(cnl)
        assert result.is_valid, result.errors
        assert len(result.conditional_blocks) == 1

    def test_store(self):
        result = parse_cnl('Store text of "Status" Label as "statusVar";')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.STORE
        assert step.variable_name == "statusVar"

    def test_select_basic(self):
        result = parse_cnl('Select "Active" in "Status" Dropdown;')
        assert result.is_valid
        step = result.steps[0]
        assert step.action_type == ActionType.SELECT
        assert step.value == "Active"
