"""ActionBuilder — converts raw event payloads + context into RecordedAction models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sat.core.models import ActionType, RecordedAction
from sat.recorder.selector_extractor import build_selector_from_event


class ActionBuilder:
    """Builds :class:`RecordedAction` objects from CDP event data."""

    def build_click(
        self,
        data: dict[str, Any],
        step: int,
        url: str,
        tab_id: str,
        screenshot_path: str | None = None,
        dom_snapshot_path: str | None = None,
        cnl_step: str | None = None,
    ) -> RecordedAction:
        selector = build_selector_from_event(data)
        return RecordedAction(
            step_number=step,
            timestamp=datetime.utcnow(),
            action_type=ActionType.CLICK,
            url=url,
            tab_id=tab_id,
            selector=selector,
            value=None,
            screenshot_path=screenshot_path,
            dom_snapshot_path=dom_snapshot_path,
            viewport=data.get("viewport", {}),
            element_position=data.get("rect"),
            metadata={
                "clientX": data.get("clientX"),
                "clientY": data.get("clientY"),
            },
            cnl_step=cnl_step,
        )

    def build_type(
        self,
        data: dict[str, Any],
        step: int,
        url: str,
        tab_id: str,
        screenshot_path: str | None = None,
        dom_snapshot_path: str | None = None,
        cnl_step: str | None = None,
    ) -> RecordedAction:
        selector = build_selector_from_event(data)
        return RecordedAction(
            step_number=step,
            timestamp=datetime.utcnow(),
            action_type=ActionType.TYPE,
            url=url,
            tab_id=tab_id,
            selector=selector,
            value=data.get("value"),
            screenshot_path=screenshot_path,
            dom_snapshot_path=dom_snapshot_path,
            viewport=data.get("viewport", {}),
            element_position=data.get("rect"),
            cnl_step=cnl_step,
        )

    def build_select(
        self,
        data: dict[str, Any],
        step: int,
        url: str,
        tab_id: str,
        screenshot_path: str | None = None,
        dom_snapshot_path: str | None = None,
        cnl_step: str | None = None,
    ) -> RecordedAction:
        selector = build_selector_from_event(data)
        return RecordedAction(
            step_number=step,
            timestamp=datetime.utcnow(),
            action_type=ActionType.SELECT,
            url=url,
            tab_id=tab_id,
            selector=selector,
            value=data.get("value"),
            screenshot_path=screenshot_path,
            dom_snapshot_path=dom_snapshot_path,
            viewport=data.get("viewport", {}),
            element_position=data.get("rect"),
            metadata={"selectedText": data.get("selectedText")},
            cnl_step=cnl_step,
        )

    def build_navigate(
        self,
        url: str,
        step: int,
        tab_id: str,
        cnl_step: str | None = None,
    ) -> RecordedAction:
        return RecordedAction(
            step_number=step,
            timestamp=datetime.utcnow(),
            action_type=ActionType.NAVIGATE,
            url=url,
            tab_id=tab_id,
            value=url,
            cnl_step=cnl_step,
        )

    def build_new_tab(
        self,
        url: str,
        step: int,
        tab_id: str,
        cnl_step: str | None = None,
    ) -> RecordedAction:
        return RecordedAction(
            step_number=step,
            timestamp=datetime.utcnow(),
            action_type=ActionType.NEW_TAB,
            url=url,
            tab_id=tab_id,
            value=url,
            cnl_step=cnl_step,
        )

    def build_switch_tab(
        self,
        url: str,
        title: str,
        step: int,
        tab_id: str,
        cnl_step: str | None = None,
    ) -> RecordedAction:
        return RecordedAction(
            step_number=step,
            timestamp=datetime.utcnow(),
            action_type=ActionType.SWITCH_TAB,
            url=url,
            tab_id=tab_id,
            value=url,
            metadata={"title": title},
            cnl_step=cnl_step,
        )

    def build_close_tab(
        self,
        url: str,
        step: int,
        tab_id: str,
        cnl_step: str | None = None,
    ) -> RecordedAction:
        return RecordedAction(
            step_number=step,
            timestamp=datetime.utcnow(),
            action_type=ActionType.CLOSE_TAB,
            url=url,
            tab_id=tab_id,
            cnl_step=cnl_step,
        )
