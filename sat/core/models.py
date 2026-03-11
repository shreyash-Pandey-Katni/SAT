"""Core Pydantic data models for SAT."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    CLICK = "click"
    TYPE = "type"
    NAVIGATE = "navigate"      # user-initiated URL change ONLY
    NEW_TAB = "new_tab"
    SWITCH_TAB = "switch_tab"
    CLOSE_TAB = "close_tab"
    SCROLL = "scroll"
    SELECT = "select"
    HOVER = "hover"
    STORE = "store"            # capture element text into a variable
    ASSERT = "assert"          # validate element state or content
    CHECK = "check"            # ensure checkbox/radio is checked
    UNCHECK = "uncheck"        # ensure checkbox/radio is unchecked


class StepResult(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ResolutionMethod(str, Enum):
    SELECTOR = "selector"
    EMBEDDING = "embedding"
    OCR = "ocr"
    VLM = "vlm"
    NONE = "none"


class ExecutionStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# Selector / element info
# ---------------------------------------------------------------------------


class SelectorInfo(BaseModel):
    tag_name: str
    css: str | None = None
    xpath: str | None = None
    id: str | None = None
    name: str | None = None
    class_name: str | None = None
    text_content: str | None = None
    aria_label: str | None = None
    placeholder: str | None = None
    data_testid: str | None = None
    href: str | None = None
    role: str | None = None
    input_type: str | None = None            # type attr for <input>
    outer_html_snippet: str = ""             # truncated outerHTML for embedding context
    parent_html_snippet: str | None = None
    # iframe context — None means top-level frame
    frame_url: str | None = None
    # True when the element lives inside a shadow root
    in_shadow_dom: bool = False


# ---------------------------------------------------------------------------
# Auto-heal tracking
# ---------------------------------------------------------------------------


class HealRecord(BaseModel):
    healed_at: datetime = Field(default_factory=_utc_now)
    healed_by: str                           # "embedding" | "ocr" | "vlm"
    similarity_score: float | None = None
    previous_selector: SelectorInfo
    new_selector: SelectorInfo


# ---------------------------------------------------------------------------
# CNL step
# ---------------------------------------------------------------------------


class CNLStep(BaseModel):
    step_number: int
    raw_cnl: str                             # e.g. 'Click "Log in" Button;'
    action_type: ActionType
    element_query: str                       # Used as embedding query
    value: str | None = None                 # For type/select actions
    element_type_hint: str | None = None     # "Button", "TextField", etc.


# ---------------------------------------------------------------------------
# Recorded action
# ---------------------------------------------------------------------------


class RecordedAction(BaseModel):
    step_number: int
    timestamp: datetime = Field(default_factory=_utc_now)
    action_type: ActionType
    url: str
    tab_id: str
    selector: SelectorInfo | None = None
    value: str | None = None
    screenshot_path: str | None = None
    dom_snapshot_path: str | None = None
    viewport: dict[str, Any] = Field(default_factory=dict)
    element_position: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # CNL
    cnl_step: str | None = None              # e.g. 'Click "Log in" Button;'
    # Auto-heal
    heal_history: list[HealRecord] = Field(default_factory=list)
    last_healed: datetime | None = None


# ---------------------------------------------------------------------------
# Recorded test
# ---------------------------------------------------------------------------


class RecordedTest(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=_utc_now)
    start_url: str
    browser: str = "chromium"
    actions: list[RecordedAction] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # CNL
    cnl: str | None = None                   # Full CNL text block
    cnl_steps: list[CNLStep] = Field(default_factory=list)
    # Variables
    variables_file: str | None = None        # per-test variables TOML path
    # Versioning
    branch: str = "main"                     # named branch


# ---------------------------------------------------------------------------
# Execution results
# ---------------------------------------------------------------------------


class ExecutionStepResult(BaseModel):
    class ResolutionAttempt(BaseModel):
        strategy: ResolutionMethod
        success: bool
        score: float | None = None
        error: str | None = None
        duration_ms: int

    step_number: int
    action: RecordedAction
    cnl_step: str | None = None
    result: StepResult
    resolution_method: ResolutionMethod | None = None
    similarity_score: float | None = None
    expected_url: str | None = None
    actual_url: str | None = None
    resolution_trace: list[ResolutionAttempt] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int
    screenshot_path: str | None = None
    healed: bool = False


class ExecutionReport(BaseModel):
    class ExecutionEnvironment(BaseModel):
        browser: str
        headless: bool
        viewport: dict[str, int]
        strategies: list[str] = Field(default_factory=list)
        auto_heal: bool
        os: str
        sat_version: str

    id: str
    test_id: str
    test_name: str
    executed_at: datetime = Field(default_factory=_utc_now)
    ended_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.PASSED
    start_url: str | None = None
    branch: str = "main"                     # branch this execution ran on
    total_steps: int
    passed: int
    failed: int
    skipped: int
    duration_s: float
    steps: list[ExecutionStepResult] = Field(default_factory=list)
    healed_steps: int = 0
    resolution_summary: dict[str, int] = Field(default_factory=dict)
    environment: ExecutionEnvironment | None = None
