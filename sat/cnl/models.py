"""CNL data models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from sat.core.models import ActionType


# ---------------------------------------------------------------------------
# Conditional support
# ---------------------------------------------------------------------------


class ConditionType(str, Enum):
    IS_VISIBLE = "is_visible"
    IS_HIDDEN = "is_hidden"
    CONTAINS_TEXT = "contains_text"
    IS_EQUAL = "is_equal"


class CNLCondition(BaseModel):
    """A single conditional expression."""
    element_query: str
    element_type_hint: str | None = None
    condition_type: ConditionType
    expected_value: str | None = None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


class CNLStep(BaseModel):
    """A single parsed CNL instruction."""

    step_number: int
    raw_cnl: str                            # Original line, e.g. 'Click "Log in" Button;'
    action_type: ActionType
    element_query: str                      # Used as embedding / VLM query
    value: str | None = None               # Typed text / URL / selected option
    element_type_hint: str | None = None   # "Button", "TextField", etc.
    # Store-specific
    variable_name: str | None = None       # For STORE: name of variable to store into
    store_attribute: str | None = None     # "text" (default) | "value" | "<attr-name>"


class CNLConditionalBlock(BaseModel):
    """A conditional block: If ... { then_steps } Else { else_steps }."""
    condition: CNLCondition
    then_steps: list[CNLStep]
    else_steps: list[CNLStep]
    start_line: int


class CNLParseError(BaseModel):
    line: int
    raw: str
    message: str


class ParsedCNL(BaseModel):
    steps: list[CNLStep]
    errors: list[CNLParseError]
    conditional_blocks: list[CNLConditionalBlock] = []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0
