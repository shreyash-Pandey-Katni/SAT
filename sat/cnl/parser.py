"""CNL parser — converts raw CNL text into CNLStep objects.

CNL Grammar (informal):
    Click "<label>" [ElementType];
    Type "<value>" in "<label>" [ElementType];
    Select "<value>" in "<label>" Dropdown;
    Navigate to "<url>";
    GoToURL "<url>";
    Open new tab "<url>";
    Switch to tab "<title>";
    Close current tab;
    Hover "<label>" [ElementType];
    Store text of "<label>" [ElementType] as "<var_name>";
    Check "<label>" [ElementType];
    Uncheck "<label>" [ElementType];

    Assert "<label>" [ElementType] is visible;
    Assert text of "<label>" [ElementType] contains "<text>";
    Verify "<label>" [ElementType] isVisible;          # Old CNL alias
    Verify isEqual <subject> "<expected>";              # Old CNL value check

    If "<label>" [ElementType] (is visible|...) {       # SAT 2.0 syntax
        <steps>
    } Else {
        <steps>
    }

    ifSatisfy                                           # Old CNL syntax
        <Verify ...>
    execute
        <Click ...>
    else
        <>

ElementType = Button | Link | TextField | Checkbox | Dropdown | Radio | Tab |
              Menu | Element | ComboBox | RadioButton | Label | ...
"""

from __future__ import annotations

import logging
import re

from sat.cnl.models import (
    CNLConditionalBlock,
    CNLCondition,
    CNLParseError,
    CNLStep,
    ConditionType,
    ParsedCNL,
)
from sat.cnl.variables import substitute as _substitute_vars
from sat.core.models import ActionType

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

_Q = r'"([^"]*)"'   # quoted string group

# Map old CNL control-type names → SAT 2.0 canonical names
_OLD_CONTROL_TYPE_MAP: dict[str, str] = {
    "combobox":    "dropdown",
    "textarea":    "textfield",
    "radiobutton": "radio",
    "div":         "element",
    "label":       "text",
    "tree":        "element",
    "list":        "element",
    "table":       "element",
    "inputfile":   "element",
    "option":      "element",
    "md-select":   "dropdown",
    "md-option":   "element",
    "select":      "dropdown",
    "span":        "text",
}

_KNOWN_ELEMENT_TYPES = {
    "button", "link", "textfield", "checkbox", "dropdown",
    "radio", "tab", "menu", "element", "image", "icon", "text",
}

# ── Regex patterns for each statement type ──────────────────────────────────

PATTERNS: list[tuple[str, ActionType, re.Pattern]] = [
    (
        "click",
        ActionType.CLICK,
        re.compile(
            rf'^\s*Click\s+{_Q}(?:\s+(\w+))?\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "type",
        ActionType.TYPE,
        re.compile(
            rf'^\s*Type\s+{_Q}\s+in\s+{_Q}(?:\s+(\w+))?\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "select",
        ActionType.SELECT,
        re.compile(
            rf'^\s*Select\s+{_Q}\s+in\s+{_Q}(?:\s+(\w+))?\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "navigate",
        ActionType.NAVIGATE,
        re.compile(
            rf'^\s*Navigate\s+to\s+{_Q}\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "go_to_url",  # Alias for compatibility with other CNL tools
        ActionType.NAVIGATE,
        re.compile(
            rf'^\s*GoToURL\s+{_Q}\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "new_tab",
        ActionType.NEW_TAB,
        re.compile(
            rf'^\s*Open\s+new\s+tab\s+{_Q}\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "switch_tab",
        ActionType.SWITCH_TAB,
        re.compile(
            rf'^\s*Switch\s+to\s+tab\s+{_Q}\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "close_tab",
        ActionType.CLOSE_TAB,
        re.compile(
            r'^\s*Close\s+current\s+tab\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "hover",
        ActionType.HOVER,
        re.compile(
            rf'^\s*Hover\s+{_Q}(?:\s+(\w+))?\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "store",
        ActionType.STORE,
        re.compile(
            rf'^\s*Store\s+(\w+)\s+of\s+{_Q}(?:\s+(\w+))?\s+as\s+{_Q}\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # ── Check / Uncheck ──────────────────────────────────────────────────
    (
        "check",
        ActionType.CHECK,
        re.compile(
            rf'^\s*Check\s+{_Q}(?:\s+(\w+))?\s*;\s*$', re.IGNORECASE
        ),
    ),
    (
        "uncheck",
        ActionType.UNCHECK,
        re.compile(
            rf'^\s*Uncheck\s+{_Q}(?:\s+(\w+))?\s*;\s*$', re.IGNORECASE
        ),
    ),
    # ── SAT 2.0 Assert patterns ─────────────────────────────────────────
    (
        "assert_visible",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Assert\s+{_Q}(?:\s+(\w+))?\s+is\s+visible\s*;\s*$',
            re.IGNORECASE
        ),
    ),
    (
        "assert_hidden",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Assert\s+{_Q}(?:\s+(\w+))?\s+is\s+hidden\s*;\s*$',
            re.IGNORECASE
        ),
    ),
    (
        "assert_text_contains",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Assert\s+text\s+of\s+{_Q}(?:\s+(\w+))?\s+contains\s+{_Q}\s*;\s*$',
            re.IGNORECASE
        ),
    ),
    (
        "assert_text_equal",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Assert\s+text\s+of\s+{_Q}(?:\s+(\w+))?\s+isEqual\s+{_Q}\s*;\s*$',
            re.IGNORECASE
        ),
    ),
    (
        "assert_value_contains",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Assert\s+value\s+of\s+{_Q}(?:\s+(\w+))?\s+contains\s+{_Q}\s*;\s*$',
            re.IGNORECASE
        ),
    ),
    (
        "assert_value_equal",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Assert\s+value\s+of\s+{_Q}(?:\s+(\w+))?\s+isEqual\s+{_Q}\s*;\s*$',
            re.IGNORECASE
        ),
    ),
    # ── Old CNL Verify patterns (→ ASSERT) ──────────────────────────────
    # Verify "label" Type isVisible;  /  Verify "label" Type isPresent;
    (
        "verify_visible",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+{_Q}(?:\s+(\w+))?\s+(?:isVisible|isPresent)\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify "label" Type isHidden;  /  isNotVisible  /  isNotPresent
    (
        "verify_hidden",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+{_Q}(?:\s+(\w+))?\s+(?:isHidden|isNotVisible|isNotPresent)\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify "label" Type isEnabled;
    (
        "verify_enabled",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+{_Q}(?:\s+(\w+))?\s+isEnabled\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify "label" Type isNotEnabled / isDisabled;
    (
        "verify_disabled",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+{_Q}(?:\s+(\w+))?\s+(?:isNotEnabled|isDisabled)\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify text of "label" Type contains "expected";
    (
        "verify_text_contains",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+text\s+of\s+{_Q}(?:\s+(\w+))?\s+contains\s+{_Q}\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify text of "label" Type isEqual "expected";
    (
        "verify_text_equal",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+text\s+of\s+{_Q}(?:\s+(\w+))?\s+isEqual\s+{_Q}\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify isEqual <subject> "expected";  — value/variable comparison
    (
        "verify_isequal_value",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+isEqual\s+(\S+)\s+{_Q}\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify contains <subject> "expected";  — value/variable contains
    (
        "verify_contains_value",
        ActionType.ASSERT,
        re.compile(
            rf'^\s*Verify\s+contains\s+(\S+)\s+{_Q}\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify unquoted-label Type isVisible;  e.g. Verify Dashboard Label isVisible;
    (
        "verify_unquoted_visible",
        ActionType.ASSERT,
        re.compile(
            r'^\s*Verify\s+(.+?)\s+(\w+)\s+(?:isVisible|isPresent)\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
    # Verify unquoted-label Type isHidden/isNotVisible/isNotPresent;
    (
        "verify_unquoted_hidden",
        ActionType.ASSERT,
        re.compile(
            r'^\s*Verify\s+(.+?)\s+(\w+)\s+(?:isHidden|isNotVisible|isNotPresent)\s*;\s*$',
            re.IGNORECASE,
        ),
    ),
]

# ── SAT 2.0 Conditional regex ────────────────────────────────────────────────
# If "<label>" [ElementType] <condition> {
_IF_RE = re.compile(
    rf'^\s*If\s+{_Q}(?:\s+(\w+))?\s+'
    r'(is\s+visible|is\s+hidden|contains\s+"([^"]*)"|isEqual\s+"([^"]*)")'
    r'\s*\{\s*$',
    re.IGNORECASE,
)
_ELSE_RE = re.compile(r'^\s*\}\s*Else\s*\{\s*$', re.IGNORECASE)
_CLOSE_BRACE_RE = re.compile(r'^\s*\}\s*$')

# ── Old CNL IfSatisfy block regex ────────────────────────────────────────────
_IFSATISFY_RE = re.compile(r'^\s*ifSatisfy\s*$', re.IGNORECASE)
_EXECUTE_RE = re.compile(r'^\s*execute\s*$', re.IGNORECASE)
_ELSE_LEGACY_RE = re.compile(r'^\s*else\s*$', re.IGNORECASE)
_ANGLE_CMD_RE = re.compile(r'^\s*<(.*)>\s*;?\s*$')

# Old CNL Verify condition patterns (inside angle brackets, no semicolon)
# <Verify "label" Type isVisible>
_VERIFY_COND_QUOTED_RE = re.compile(
    rf'^\s*Verify\s+{_Q}(?:\s+(\w+))?\s+'
    r'(isVisible|isPresent|isHidden|isNotVisible|isNotPresent|isEnabled|isNotEnabled|isDisabled)'
    r'\s*$',
    re.IGNORECASE,
)
# <Verify unquoted-label Type isVisible>
_VERIFY_COND_UNQUOTED_RE = re.compile(
    r'^\s*Verify\s+(.+?)\s+(\w+)\s+'
    r'(isVisible|isPresent|isHidden|isNotVisible|isNotPresent|isEnabled|isNotEnabled|isDisabled)'
    r'\s*$',
    re.IGNORECASE,
)
# <Verify isEqual subject "expected">
_VERIFY_COND_ISEQUAL_RE = re.compile(
    rf'^\s*Verify\s+isEqual\s+(\S+)\s+{_Q}\s*$',
    re.IGNORECASE,
)
# <Verify contains subject "expected">
_VERIFY_COND_CONTAINS_RE = re.compile(
    rf'^\s*Verify\s+contains\s+(\S+)\s+{_Q}\s*$',
    re.IGNORECASE,
)
# <Verify text of "label" Type contains "expected">
_VERIFY_COND_TEXT_CMP_RE = re.compile(
    rf'^\s*Verify\s+text\s+of\s+{_Q}(?:\s+(\w+))?\s+(contains|isEqual)\s+{_Q}\s*$',
    re.IGNORECASE,
)


def parse_cnl(text: str, variables: dict[str, str] | None = None) -> ParsedCNL:
    """Parse a multi-line CNL string into a :class:`ParsedCNL` result.

    Supports both **SAT 2.0** syntax and the **old CNL** syntax
    (``Verify … isVisible``, ``IfSatisfy`` blocks, ``Check``/``Uncheck``,
    etc.).  Old-style commands are transparently mapped to their SAT 2.0
    equivalents so the executor can handle them uniformly.

    If *variables* is provided, ``${var}`` placeholders are replaced
    before parsing.  Lines starting with '#' are comments.  Blank
    lines are ignored.
    """
    if variables:
        text = _substitute_vars(text, variables)

    lines = text.splitlines()
    steps: list[CNLStep] = []
    errors: list[CNLParseError] = []
    conditional_blocks: list[CNLConditionalBlock] = []
    step_num = 0
    idx = 0

    while idx < len(lines):
        lineno = idx + 1
        raw_line = lines[idx]
        line = raw_line.strip()

        if not line or line.startswith("#"):
            idx += 1
            continue

        # ── Try SAT 2.0 conditional block  If ... { ─────────────────
        m_if = _IF_RE.match(line)
        if m_if:
            block, block_errors, consumed, step_num = _parse_conditional_block(
                lines, idx, step_num,
            )
            if block is not None:
                conditional_blocks.append(block)
                steps.extend(block.then_steps)
                steps.extend(block.else_steps)
            errors.extend(block_errors)
            idx += consumed
            continue

        # ── Try old CNL IfSatisfy block ──────────────────────────────
        if _IFSATISFY_RE.match(line):
            block, block_errors, consumed, step_num = _parse_ifsatisfy_block(
                lines, idx, step_num,
            )
            if block is not None:
                conditional_blocks.append(block)
                steps.extend(block.then_steps)
                steps.extend(block.else_steps)
            errors.extend(block_errors)
            idx += consumed
            continue

        # ── Regular statement ────────────────────────────────────────
        step_num += 1
        step = _parse_line(line, step_num, lineno)
        if step is None:
            errors.append(
                CNLParseError(
                    line=lineno, raw=raw_line,
                    message=f"Unrecognised CNL statement: {line!r}",
                )
            )
        else:
            steps.append(step)
        idx += 1

    return ParsedCNL(steps=steps, errors=errors, conditional_blocks=conditional_blocks)


# ── SAT 2.0 Conditional block parser ────────────────────────────────────────

def _parse_conditional_block(
    lines: list[str], start_idx: int, step_num: int,
) -> tuple[CNLConditionalBlock | None, list[CNLParseError], int, int]:
    """Parse an If/Else block starting at *start_idx*.

    Returns (block, errors, lines_consumed, updated_step_num).
    """
    errors: list[CNLParseError] = []
    lineno = start_idx + 1
    line = lines[start_idx].strip()
    m = _IF_RE.match(line)
    if not m:
        errors.append(CNLParseError(line=lineno, raw=lines[start_idx], message="Invalid If syntax"))
        return None, errors, 1, step_num

    label = m.group(1)
    el_type = m.group(2)
    cond_raw = m.group(3)
    contains_val = m.group(4)
    equal_val = m.group(5)

    cond_type, expected = _parse_condition_keyword(cond_raw, contains_val, equal_val)
    condition = CNLCondition(
        element_query=_build_query(label, el_type),
        element_type_hint=_normalise_type(el_type),
        condition_type=cond_type,
        expected_value=expected,
    )

    idx = start_idx + 1
    then_steps: list[CNLStep] = []
    else_steps: list[CNLStep] = []
    in_else = False

    while idx < len(lines):
        lineno = idx + 1
        raw = lines[idx]
        stripped = raw.strip()

        if not stripped or stripped.startswith("#"):
            idx += 1
            continue

        if _ELSE_RE.match(stripped):
            in_else = True
            idx += 1
            continue

        if _CLOSE_BRACE_RE.match(stripped):
            idx += 1
            return (
                CNLConditionalBlock(
                    condition=condition,
                    then_steps=then_steps,
                    else_steps=else_steps,
                    start_line=start_idx + 1,
                ),
                errors,
                idx - start_idx,
                step_num,
            )

        step_num += 1
        step = _parse_line(stripped, step_num, lineno)
        if step is None:
            errors.append(CNLParseError(
                line=lineno, raw=raw,
                message=f"Unrecognised CNL inside block: {stripped!r}",
            ))
        else:
            if in_else:
                else_steps.append(step)
            else:
                then_steps.append(step)
        idx += 1

    # Unterminated block
    errors.append(CNLParseError(
        line=start_idx + 1, raw=lines[start_idx],
        message="Unterminated If block — missing closing '}'",
    ))
    return None, errors, idx - start_idx, step_num


# ── Old CNL IfSatisfy block parser ──────────────────────────────────────────

def _parse_ifsatisfy_block(
    lines: list[str], start_idx: int, step_num: int,
) -> tuple[CNLConditionalBlock | None, list[CNLParseError], int, int]:
    """Parse an old-style ``ifSatisfy … execute … else`` block.

    Structure (indentation-scoped)::

        ifSatisfy
            <Verify "label" Type isVisible>      ← condition (angle-bracket)
        execute
            <Click "Submit" Button>              ← then-branch commands
        else                                     ← optional
            <>                                   ← no-op / else commands

    The block ends when we see a line that is not an angle-bracket
    command and is not ``execute`` / ``else``, or at EOF.

    Returns (block, errors, lines_consumed, updated_step_num).
    """
    errors: list[CNLParseError] = []
    idx = start_idx + 1  # skip the 'ifSatisfy' line itself

    # States: CONDITION → EXECUTE → ELSE
    state = "CONDITION"
    condition_commands: list[str] = []
    then_steps: list[CNLStep] = []
    else_steps: list[CNLStep] = []

    while idx < len(lines):
        lineno = idx + 1
        raw = lines[idx]
        stripped = raw.strip()

        # Skip blank / comment lines
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue

        # State transitions on keywords
        if _EXECUTE_RE.match(stripped):
            state = "EXECUTE"
            idx += 1
            continue

        if _ELSE_LEGACY_RE.match(stripped):
            state = "ELSE"
            idx += 1
            continue

        # Angle-bracket command?
        m_angle = _ANGLE_CMD_RE.match(stripped)
        if m_angle:
            inner = m_angle.group(1).strip()
            if not inner:
                # <> is a no-op
                idx += 1
                continue

            if state == "CONDITION":
                condition_commands.append(inner)
            elif state == "EXECUTE":
                step_num += 1
                step = _parse_angle_command(inner, step_num, lineno)
                if step is None:
                    errors.append(CNLParseError(
                        line=lineno, raw=raw,
                        message=f"Unrecognised command inside ifSatisfy execute: {inner!r}",
                    ))
                else:
                    then_steps.append(step)
            elif state == "ELSE":
                step_num += 1
                step = _parse_angle_command(inner, step_num, lineno)
                if step is None:
                    errors.append(CNLParseError(
                        line=lineno, raw=raw,
                        message=f"Unrecognised command inside ifSatisfy else: {inner!r}",
                    ))
                else:
                    else_steps.append(step)
            idx += 1
            continue

        # Non-angle, non-keyword line → block has ended
        break

    # Build the condition from collected condition commands
    condition = _build_ifsatisfy_condition(
        condition_commands, start_idx + 1, lines[start_idx], errors,
    )
    if condition is None:
        return None, errors, idx - start_idx, step_num

    block = CNLConditionalBlock(
        condition=condition,
        then_steps=then_steps,
        else_steps=else_steps,
        start_line=start_idx + 1,
    )
    return block, errors, idx - start_idx, step_num


def _build_ifsatisfy_condition(
    condition_commands: list[str],
    lineno: int,
    raw_line: str,
    errors: list[CNLParseError],
) -> CNLCondition | None:
    """Extract a :class:`CNLCondition` from the condition commands of an
    ``ifSatisfy`` block.

    The condition section typically contains a single ``Verify`` command
    (without angle brackets — those have already been stripped).
    """
    if not condition_commands:
        errors.append(CNLParseError(
            line=lineno, raw=raw_line,
            message="ifSatisfy block has no condition (expected <Verify …>)",
        ))
        return None

    # Use the first condition command (typically there's just one)
    cmd = condition_commands[0]

    # ── Try: Verify isEqual <subject> "expected" ─────────────────────
    m = _VERIFY_COND_ISEQUAL_RE.match(cmd)
    if m:
        subject, expected = m.group(1), m.group(2)
        return CNLCondition(
            element_query=subject,
            element_type_hint=None,
            condition_type=ConditionType.IS_EQUAL,
            expected_value=expected,
        )

    # ── Try: Verify contains <subject> "expected" ────────────────────
    m = _VERIFY_COND_CONTAINS_RE.match(cmd)
    if m:
        subject, expected = m.group(1), m.group(2)
        return CNLCondition(
            element_query=subject,
            element_type_hint=None,
            condition_type=ConditionType.CONTAINS_TEXT,
            expected_value=expected,
        )

    # ── Try: Verify text of "label" Type (contains|isEqual) "expected"
    m = _VERIFY_COND_TEXT_CMP_RE.match(cmd)
    if m:
        label, el_type, op, expected = m.group(1), m.group(2), m.group(3), m.group(4)
        cond_type = (
            ConditionType.IS_EQUAL if op.lower() == "isequal"
            else ConditionType.CONTAINS_TEXT
        )
        return CNLCondition(
            element_query=_build_query(label, el_type),
            element_type_hint=_normalise_type(el_type),
            condition_type=cond_type,
            expected_value=expected,
        )

    # ── Try: Verify "label" Type isVisible/isHidden/etc ──────────────
    m = _VERIFY_COND_QUOTED_RE.match(cmd)
    if m:
        label, el_type, kw = m.group(1), m.group(2), m.group(3)
        cond_type = _keyword_to_condition_type(kw)
        return CNLCondition(
            element_query=_build_query(label, el_type),
            element_type_hint=_normalise_type(el_type),
            condition_type=cond_type,
            expected_value=None,
        )

    # ── Try: Verify unquoted-label Type isVisible/isHidden/etc ───────
    m = _VERIFY_COND_UNQUOTED_RE.match(cmd)
    if m:
        label, el_type, kw = m.group(1).strip(), m.group(2), m.group(3)
        cond_type = _keyword_to_condition_type(kw)
        return CNLCondition(
            element_query=_build_query(label, el_type),
            element_type_hint=_normalise_type(el_type),
            condition_type=cond_type,
            expected_value=None,
        )

    # Could not parse the condition — report error but don't crash
    errors.append(CNLParseError(
        line=lineno, raw=raw_line,
        message=f"Could not parse ifSatisfy condition: {cmd!r}",
    ))
    return None


def _keyword_to_condition_type(keyword: str) -> ConditionType:
    """Map old CNL condition keywords to :class:`ConditionType`."""
    kw = keyword.lower()
    if kw in ("isvisible", "ispresent"):
        return ConditionType.IS_VISIBLE
    if kw in ("ishidden", "isnotvisible", "isnotpresent"):
        return ConditionType.IS_HIDDEN
    if kw == "isenabled":
        return ConditionType.IS_ENABLED
    if kw in ("isnotenabled", "isdisabled"):
        return ConditionType.IS_DISABLED
    return ConditionType.IS_VISIBLE  # fallback


def _parse_angle_command(
    inner: str, step_num: int, lineno: int,
) -> CNLStep | None:
    """Parse the inner content of an angle-bracket command ``<…>``.

    The inner text uses the same syntax as regular CNL statements but
    **without** a trailing semicolon.  We append one so ``_parse_line``
    can match it, then strip it from the ``raw_cnl`` stored on the step.
    """
    # Try with semicolon appended (most patterns require it)
    normalised = inner.rstrip(";").strip() + ";"
    step = _parse_line(normalised, step_num, lineno)
    if step is not None:
        # Store the original angle-bracket form in raw_cnl
        return step.model_copy(update={"raw_cnl": f"<{inner}>"})
    return None


# ── Condition keyword helper (for SAT 2.0 If blocks) ────────────────────────

def _parse_condition_keyword(
    raw: str, contains_val: str | None, equal_val: str | None,
) -> tuple[ConditionType, str | None]:
    low = raw.lower().strip()
    if low.startswith("is visible"):
        return ConditionType.IS_VISIBLE, None
    if low.startswith("is hidden"):
        return ConditionType.IS_HIDDEN, None
    if low.startswith("contains"):
        return ConditionType.CONTAINS_TEXT, contains_val
    if low.startswith("isequal"):
        return ConditionType.IS_EQUAL, equal_val
    return ConditionType.IS_VISIBLE, None


# ── Private — line parser ────────────────────────────────────────────────────


def _parse_line(line: str, step_num: int, lineno: int) -> CNLStep | None:
    for kind, action_type, pattern in PATTERNS:
        m = pattern.match(line)
        if m is None:
            continue
        groups = m.groups()
        return _build_step(kind, action_type, line, step_num, groups)
    return None


def _build_step(
    kind: str,
    action_type: ActionType,
    raw_cnl: str,
    step_num: int,
    groups: tuple,
) -> CNLStep:
    match kind:
        case "click":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                value=None,
                element_type_hint=_normalise_type(el_type),
            )
        case "type":
            value, label, el_type = groups[0], groups[1], groups[2]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                value=value,
                element_type_hint=_normalise_type(el_type),
            )
        case "select":
            value, label, el_type = groups[0], groups[1], groups[2]
            # Parse select mode: "index=N" | "value=X" | plain text
            select_mode = "text"
            if value.startswith("index="):
                select_mode = "index"
                value = value[len("index="):]
            elif value.startswith("value="):
                select_mode = "value"
                value = value[len("value="):]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                value=value,
                element_type_hint="Dropdown",
                select_mode=select_mode,
            )
        case "navigate" | "go_to_url":
            url = groups[0]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=url,
                value=url,
            )
        case "new_tab":
            url = groups[0]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=url,
                value=url,
            )
        case "switch_tab":
            title = groups[0]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=title,
                value=title,
            )
        case "close_tab":
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query="",
            )
        case "hover":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                value=None,
                element_type_hint=_normalise_type(el_type),
            )
        case "store":
            attribute, label, el_type, var_name = groups
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                value=None,
                element_type_hint=_normalise_type(el_type),
                variable_name=var_name,
                store_attribute=attribute.lower(),
            )
        # ── Check / Uncheck ──────────────────────────────────────────
        case "check" | "uncheck":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                value=None,
                element_type_hint=_normalise_type(el_type) or "Checkbox",
            )
        # ── SAT 2.0 Assert patterns ─────────────────────────────────
        case "assert_visible":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_VISIBLE,
                assertion_expected=None,
            )
        case "assert_hidden":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_HIDDEN,
                assertion_expected=None,
            )
        case "assert_text_contains":
            label, el_type, expected = groups[0], groups[1], groups[2]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.CONTAINS_TEXT,
                assertion_expected=expected,
                store_attribute="text",
            )
        case "assert_text_equal":
            label, el_type, expected = groups[0], groups[1], groups[2]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_EQUAL,
                assertion_expected=expected,
                store_attribute="text",
            )
        case "assert_value_contains":
            label, el_type, expected = groups[0], groups[1], groups[2]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.CONTAINS_TEXT,
                assertion_expected=expected,
                store_attribute="value",
            )
        case "assert_value_equal":
            label, el_type, expected = groups[0], groups[1], groups[2]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_EQUAL,
                assertion_expected=expected,
                store_attribute="value",
            )
        # ── Old CNL Verify patterns (→ ASSERT) ──────────────────────
        case "verify_visible":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_VISIBLE,
                assertion_expected=None,
            )
        case "verify_hidden":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_HIDDEN,
                assertion_expected=None,
            )
        case "verify_enabled":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_ENABLED,
                assertion_expected=None,
            )
        case "verify_disabled":
            label, el_type = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_DISABLED,
                assertion_expected=None,
            )
        case "verify_text_contains":
            label, el_type, expected = groups[0], groups[1], groups[2]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.CONTAINS_TEXT,
                assertion_expected=expected,
                store_attribute="text",
            )
        case "verify_text_equal":
            label, el_type, expected = groups[0], groups[1], groups[2]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_EQUAL,
                assertion_expected=expected,
                store_attribute="text",
            )
        case "verify_isequal_value":
            subject, expected = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=subject,
                element_type_hint=None,
                assertion_type=ConditionType.IS_EQUAL,
                assertion_expected=expected,
            )
        case "verify_contains_value":
            subject, expected = groups[0], groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=subject,
                element_type_hint=None,
                assertion_type=ConditionType.CONTAINS_TEXT,
                assertion_expected=expected,
            )
        case "verify_unquoted_visible":
            label, el_type = groups[0].strip(), groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_VISIBLE,
                assertion_expected=None,
            )
        case "verify_unquoted_hidden":
            label, el_type = groups[0].strip(), groups[1]
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                element_type_hint=_normalise_type(el_type),
                assertion_type=ConditionType.IS_HIDDEN,
                assertion_expected=None,
            )
        case _:
            raise ValueError(f"Unknown kind: {kind}")


# ── Private — helpers ────────────────────────────────────────────────────────


def _build_query(label: str, el_type: str | None) -> str:
    """Build the element query string from label and optional element type."""
    if el_type and _resolve_type(el_type) is not None:
        return f"{label} {el_type}"
    return label


def _normalise_type(el_type: str | None) -> str | None:
    """Normalise an element type to its canonical SAT 2.0 form.

    Handles both SAT 2.0 types and old CNL control-type names
    (e.g. ComboBox → Dropdown, RadioButton → Radio).
    """
    if not el_type:
        return None
    resolved = _resolve_type(el_type)
    if resolved:
        return resolved.capitalize()
    return None


def _resolve_type(el_type: str) -> str | None:
    """Return the canonical lowercase type name, or None if unknown."""
    low = el_type.lower()
    # Check old CNL mapping first
    if low in _OLD_CONTROL_TYPE_MAP:
        return _OLD_CONTROL_TYPE_MAP[low]
    # Check known SAT 2.0 types
    if low in _KNOWN_ELEMENT_TYPES:
        return low
    return None
