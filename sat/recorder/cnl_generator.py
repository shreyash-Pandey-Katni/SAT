"""CNLGenerator — auto-generates CNL descriptions from RecordedAction objects."""

from __future__ import annotations

from sat.core.models import ActionType, RecordedAction, SelectorInfo


class CNLGenerator:
    """Converts a :class:`RecordedAction` into a human-readable CNL string."""

    def generate(self, action: RecordedAction) -> str:
        match action.action_type:
            case ActionType.CLICK:
                label = self._best_label(action.selector)
                el_type = self._element_type(action.selector)
                return f'Click "{label}" {el_type};'

            case ActionType.TYPE:
                label = self._best_label(action.selector)
                el_type = self._element_type(action.selector)
                value = action.value or ""
                return f'Type "{value}" in "{label}" {el_type};'

            case ActionType.SELECT:
                label = self._best_label(action.selector)
                meta = action.metadata or {}
                selected_text = meta.get("selectedText") or action.value or ""
                return f'Select "{selected_text}" in "{label}" Dropdown;'

            case ActionType.NAVIGATE:
                return f'Navigate to "{action.value}";'

            case ActionType.NEW_TAB:
                return f'Open new tab "{action.value}";'

            case ActionType.SWITCH_TAB:
                meta = action.metadata or {}
                title = meta.get("title") or action.value or ""
                return f'Switch to tab "{title}";'

            case ActionType.CLOSE_TAB:
                return 'Close current tab;'

            case ActionType.SCROLL:
                vp = action.viewport or {}
                return f'Scroll to ({vp.get("scrollX", 0)}, {vp.get("scrollY", 0)});'

            case ActionType.HOVER:
                label = self._best_label(action.selector)
                el_type = self._element_type(action.selector)
                return f'Hover "{label}" {el_type};'

            case ActionType.STORE:
                meta = action.metadata or {}
                var_name = meta.get("variable_name", "var")
                attr = meta.get("store_attribute", "text")
                label = self._best_label(action.selector)
                el_type = self._element_type(action.selector)
                return f'Store {attr} of "{label}" {el_type} as "{var_name}";'

            case _:
                return f'Perform {action.action_type.value};'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _best_label(self, selector: SelectorInfo | None) -> str:
        """Return the most human-meaningful label for the element."""
        if selector is None:
            return "element"
        return (
            selector.aria_label
            or selector.placeholder
            or selector.text_content
            or selector.name
            or selector.id
            or selector.tag_name
        ) or "element"

    def _element_type(self, selector: SelectorInfo | None) -> str:
        """Map tag + role + input_type to a CNL element type keyword."""
        if selector is None:
            return "Element"
        tag = (selector.tag_name or "").lower()
        role = (selector.role or "").lower()
        itype = (selector.input_type or "").lower()

        if tag == "button" or role == "button":
            return "Button"
        if tag == "a" or role == "link":
            return "Link"
        if tag == "input":
            if itype in ("text", "email", "password", "search", "tel", "url", "number"):
                return "TextField"
            if itype == "checkbox":
                return "Checkbox"
            if itype == "radio":
                return "Radio"
            if itype == "submit":
                return "Button"
        if tag == "textarea":
            return "TextField"
        if tag == "select" or role in ("listbox", "combobox"):
            return "Dropdown"
        if role in ("tab",):
            return "Tab"
        if role in ("menuitem", "menu"):
            return "Menu"
        return "Element"
