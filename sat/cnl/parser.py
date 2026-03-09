"""CNL parser — converts raw CNL text into CNLStep objects.

CNL Grammar (informal):
    Click "<label>" [ElementType];
    Type "<value>" in "<label>" [ElementType];
    Select "<value>" in "<label>" Dropdown;
    Navigate to "<url>";
    GoToURL "<url>";  # Alias for compatibility with other CNL tools
    Open new tab "<url>";
    Switch to tab "<title>";
    Close current tab;
    Hover "<label>" [ElementType];
    Store text of "<label>" [ElementType] as "<var_name>";
    Store value of "<label>" [ElementType] as "<var_name>";
    Store <attr> of "<label>" [ElementType] as "<var_name>";

    Assert "<label>" [ElementType] is visible;
    Assert "<label>" [ElementType] is hidden;
    Assert text of "<label>" [ElementType] contains "<text>";
    Assert text of "<label>" [ElementType] isEqual "<text>";
    Assert value of "<label>" [ElementType] contains "<text>";
    Assert value of "<label>" [ElementType] isEqual "<text>";

    If "<label>" [ElementType] (is visible|is hidden|contains "<text>"|isEqual "<text>") {
        <steps>
    } Else {
        <steps>
    }

ElementType = Button | Link | TextField | Checkbox | Dropdown | Radio | Tab | Menu | Element
"""

from __future__ import annotations

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

# ── Regex patterns for each statement type ──────────────────────────────────

_Q = r'"([^"]*)"'   # quoted string group

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
    # Assertion patterns
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
]

# ── Conditional regex ────────────────────────────────────────────────────────
# If "<label>" [ElementType] <condition> {
_IF_RE = re.compile(
    rf'^\s*If\s+{_Q}(?:\s+(\w+))?\s+'
    r'(is\s+visible|is\s+hidden|contains\s+"([^"]*)"|isEqual\s+"([^"]*)")'
    r'\s*\{\s*$',
    re.IGNORECASE,
)
_ELSE_RE = re.compile(r'^\s*\}\s*Else\s*\{\s*$', re.IGNORECASE)
_CLOSE_BRACE_RE = re.compile(r'^\s*\}\s*$')


_KNOWN_ELEMENT_TYPES = {
    "button", "link", "textfield", "checkbox", "dropdown",
    "radio", "tab", "menu", "element", "image", "icon", "text",
}


def parse_cnl(text: str, variables: dict[str, str] | None = None) -> ParsedCNL:
    """Parse a multi-line CNL string into a :class:`ParsedCNL` result.

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

        # ── Try conditional block ────────────────────────────────────
        m_if = _IF_RE.match(line)
        if m_if:
            block, block_errors, consumed, step_num = _parse_conditional_block(
                lines, idx, step_num,
            )
            if block is not None:
                conditional_blocks.append(block)
                # Flatten then/else steps into the main list for flat execution
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


# ── Conditional block parser ─────────────────────────────────────────────────

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

    cond_type, expected = _parse_condition(cond_raw, contains_val, equal_val)
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


def _parse_condition(
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


# ── Private ──────────────────────────────────────────────────────────────────


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
            return CNLStep(
                step_number=step_num,
                raw_cnl=raw_cnl,
                action_type=action_type,
                element_query=_build_query(label, el_type),
                value=value,
                element_type_hint="Dropdown",
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
            # Groups: (attribute, label, el_type, var_name)
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
        case _:
            raise ValueError(f"Unknown kind: {kind}")


def _build_query(label: str, el_type: str | None) -> str:
    if el_type and el_type.lower() in _KNOWN_ELEMENT_TYPES:
        return f"{label} {el_type}"
    return label


def _normalise_type(el_type: str | None) -> str | None:
    if not el_type:
        return None
    return el_type.capitalize() if el_type.lower() in _KNOWN_ELEMENT_TYPES else None
