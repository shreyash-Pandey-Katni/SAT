"""Converts raw CDP event payloads (dicts from capture.js) into SelectorInfo models."""

from __future__ import annotations

from sat.constants import OUTER_HTML_MAX_LEN, PARENT_HTML_MAX_LEN
from sat.core.models import SelectorInfo


def build_selector_from_event(data: dict) -> SelectorInfo:
    """Build a :class:`SelectorInfo` from a capture.js event payload."""
    return SelectorInfo(
        tag_name=(data.get("tag") or "unknown").lower(),
        css=data.get("css"),
        xpath=data.get("xpath"),          # None for shadow-DOM elements (set by capture.js)
        id=data.get("id") or None,
        name=data.get("name"),
        class_name=data.get("className"),
        text_content=_truncate(data.get("text"), 200),
        aria_label=data.get("ariaLabel"),
        placeholder=data.get("placeholder"),
        data_testid=data.get("dataTestId"),
        href=data.get("href"),
        role=data.get("role"),
        input_type=data.get("inputType"),
        outer_html_snippet=_truncate(data.get("outerHTML", ""), OUTER_HTML_MAX_LEN) or "",
        parent_html_snippet=_truncate(data.get("parentHTML"), PARENT_HTML_MAX_LEN),
        frame_url=data.get("frameUrl") or None,
        in_shadow_dom=bool(data.get("inShadowDom", False)),
    )


def _truncate(value: str | None, max_len: int) -> str | None:
    if not value:
        return None
    value = value.strip()
    return value[:max_len] if len(value) > max_len else value
