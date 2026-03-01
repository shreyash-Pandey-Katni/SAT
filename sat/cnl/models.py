"""CNL data models."""

from __future__ import annotations

from pydantic import BaseModel

from sat.core.models import ActionType


class CNLStep(BaseModel):
    """A single parsed CNL instruction."""

    step_number: int
    raw_cnl: str                            # Original line, e.g. 'Click "Log in" Button;'
    action_type: ActionType
    element_query: str                      # Used as embedding / VLM query
    value: str | None = None               # Typed text / URL / selected option
    element_type_hint: str | None = None   # "Button", "TextField", etc.


class CNLParseError(BaseModel):
    line: int
    raw: str
    message: str


class ParsedCNL(BaseModel):
    steps: list[CNLStep]
    errors: list[CNLParseError]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0
