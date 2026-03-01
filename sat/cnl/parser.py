"""CNL parser — converts raw CNL text into CNLStep objects.

CNL Grammar (informal):
    Click "<label>" [ElementType];
    Type "<value>" in "<label>" [ElementType];
    Select "<value>" in "<label>" Dropdown;
    Navigate to "<url>";
    Open new tab "<url>";
    Switch to tab "<title>";
    Close current tab;
    Hover "<label>" [ElementType];
    Wait <n> seconds;
    Assert "<label>" [ElementType] (is visible|is hidden|contains "<text>"|has value "<text>");

ElementType = Button | Link | TextField | Checkbox | Dropdown | Radio | Tab | Menu | Element
"""

from __future__ import annotations

import re

from sat.cnl.models import CNLParseError, CNLStep, ParsedCNL
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
]

_KNOWN_ELEMENT_TYPES = {
    "button", "link", "textfield", "checkbox", "dropdown",
    "radio", "tab", "menu", "element", "image", "icon", "text",
}


def parse_cnl(text: str) -> ParsedCNL:
    """Parse a multi-line CNL string into a :class:`ParsedCNL` result.

    Lines that start with '#' are treated as comments.
    Blank lines are ignored.
    """
    steps: list[CNLStep] = []
    errors: list[CNLParseError] = []
    step_num = 0

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        step_num += 1
        step = _parse_line(line, step_num, lineno)
        if step is None:
            errors.append(
                CNLParseError(line=lineno, raw=raw_line, message=f"Unrecognised CNL statement: {line!r}")
            )
        else:
            steps.append(step)

    return ParsedCNL(steps=steps, errors=errors)


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
        case "navigate":
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
