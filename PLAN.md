# SAT — Selenium Activity Tool (Activity Recorder & Intelligent Executor)

## Decision Log

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python | Rich ecosystem for ML/embeddings, great automation library support |
| Automation Framework | **Playwright** (over Selenium) | Native CDP WebSocket protocol (event-driven, no polling), significantly faster execution, first-class multi-tab support, built-in auto-wait, fine-grained navigation events |
| VLM | Ollama (local, e.g. LLaVA 13B) | Self-hosted, no API costs, configurable model |
| Embeddings | **Ollama** (e.g. nomic-embed-text) | Unified Ollama stack for both embeddings + VLM, no extra dependencies, fast local inference |
| Test Format | JSON | Simple, structured, easy to parse and edit |
| Browser Support | Chrome + Firefox (configurable) | Playwright supports both natively via Chromium/Firefox engines |
| Interface | CLI + Web UI | FastAPI backend + lightweight frontend |

---

## Why Playwright Over Selenium

| Requirement | Selenium BiDi | Playwright |
|---|---|---|
| Event-driven (no polling) | BiDi is still maturing; many events require workarounds or injected JS polling | **Native CDP WebSocket** — `page.on('event')` is the core architecture. Browser notifies agent instantly. |
| Fastest execution | JSON Wire Protocol = HTTP round-trip per command | **Direct WebSocket messages** — orders of magnitude fewer round-trips |
| Navigation detection (user vs click-caused) | Requires manual tracking of which click caused which navigation | **`page.on('framenavigated')` + request interception** — can distinguish user URL bar changes from programmatic navigation |
| New tab detection | Poll `driver.window_handles` or use incomplete BiDi `browsingContext.contextCreated` | **`browser.on('page')` event** — instant, reliable, event-driven |
| Tab switching | Handle-based, error-prone | **First-class `Page` objects** — each tab is a separate object, no switching needed |
| Auto-wait | Manual `WebDriverWait` everywhere | **Built-in auto-wait** on every action — fastest possible execution without sleeps |
| iframes | Manual switching with `driver.switch_to.frame()` | **`frame.locator()`** — no context switching |
| Screenshot | `get_screenshot_as_png()` — full page only, or element-level | **`page.screenshot()` + `element.screenshot()`** — both supported, fast |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     SAT CLI / Web UI                             │
├──────────────┬──────────────────────────────────┬────────────────┤
│   Recorder   │           Executor               │   Web UI       │
│  (Playwright │  ┌──────────┐  ┌──────────────┐  │  (FastAPI +    │
│   CDP Events)│  │ Strategy │  │  Auto-Heal   │  │   Frontend)    │
│              │  │ Chain    │  │  (update     │  │                │
│  - clicks    │  │          │  │   selectors) │  │  - record      │
│  - typing    │  │ 1.Select │  │              │  │  - execute     │
│  - navigate  │  │ 2.Embed  │  │              │  │  - CNL editor  │
│  - new tab   │  │ 3.VLM   │  │              │  │  - reports     │
│  - tab switch│  │ 4.Fail  │  │              │  │                │
├──────────────┴──┴──────────┘  └──────────────┘──┴────────────────┤
│                        Core Services                             │
│  ┌────────────────┐ ┌───────────────────┐ ┌────────────────────┐ │
│  │ Playwright     │ │ Ollama Embedding  │ │ Ollama VLM         │ │
│  │ Manager        │ │ Service           │ │ Service            │ │
│  │ (Event-Driven) │ │ (nomic-embed-text)│ │ (llava:13b)        │ │
│  └────────────────┘ └───────────────────┘ └────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│                     Storage / Config                             │
│  ┌────────────┐ ┌────────────────┐ ┌───────────────────────────┐ │
│  │ Test Store │ │ Config         │ │ Report Generator          │ │
│  │ (JSON +    │ │ (TOML)         │ │ (HTML/JSON reports)       │ │
│  │  CNL)      │ │                │ │                           │ │
│  └────────────┘ └────────────────┘ └───────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## Event-Driven Architecture (No Polling)

The core principle: **the browser notifies the agent**, the agent never polls.

### Playwright's Native Event System

```python
# Playwright is built on CDP WebSocket — every action is a WebSocket message,
# every result is a WebSocket message back. Zero HTTP overhead.

# 1. CLICK DETECTION (recording)
#    Inject via page.expose_function() — browser calls Python directly over CDP
await page.expose_function("__sat_on_click", on_click_handler)
await page.add_init_script("""
    document.addEventListener('click', (e) => {
        window.__sat_on_click({
            selector: computeSelector(e.target),
            tag: e.target.tagName,
            text: e.target.textContent,
            x: e.clientX, y: e.clientY,
            // ... all attributes
        });
    }, true);
""")

# 2. NAVIGATION DETECTION (user-initiated only)
#    Track: if a click/type just happened → next navigation is caused by it
#    If no recent click/type → navigation is user-initiated (URL bar change)
page.on("framenavigated", on_navigate_handler)

# 3. NEW TAB
browser.on("page", on_new_page_handler)  # Instant notification

# 4. TAB CLOSE
page.on("close", on_page_close_handler)

# 5. INPUT/TYPE DETECTION
await page.expose_function("__sat_on_input", on_input_handler)
await page.add_init_script("""
    document.addEventListener('input', (e) => {
        window.__sat_on_input({
            selector: computeSelector(e.target),
            value: e.target.value,
            // ...
        });
    }, true);
""")

# 6. EXECUTION COMPLETION
#    After executor performs an action, Playwright's auto-wait ensures
#    the action is complete. Additionally:
page.on("load", on_load_complete)
page.on("domcontentloaded", on_dom_ready)
#    For network idle:
await page.wait_for_load_state("networkidle")  # Event-driven, not polling
```

### Executor → Browser Completion Flow

```
Executor says: "click this element"
    → Playwright sends CDP command via WebSocket
    → Browser performs click
    → Browser fires CDP events back via WebSocket:
        - DOM mutation events
        - Navigation events (if click caused navigation)
        - Network events (requests triggered)
    → Playwright's auto-wait resolves
    → Executor receives completion signal (async/await resolves)
    → NO POLLING AT ANY POINT
```

---

## Navigation Recording: User-Initiated Only

**Problem:** Don't record `navigate` when it's caused by a click or type — only when the user manually changes the URL.

**Solution: Causation Tracking**

```python
class NavigationCausationTracker:
    """Tracks whether a navigation was caused by a user interaction or URL bar."""

    def __init__(self):
        self._last_interaction_time: float = 0
        self._interaction_caused_navigation = False
        self.CAUSATION_WINDOW_MS = 2000  # If nav happens within 2s of click/type,
                                          # it was caused by that interaction

    def on_user_interaction(self):
        """Called when we record a click or type action."""
        self._last_interaction_time = time.time()
        self._interaction_caused_navigation = False

    def is_user_initiated_navigation(self) -> bool:
        """Returns True if this navigation was NOT caused by a recent click/type."""
        elapsed = (time.time() - self._last_interaction_time) * 1000
        if elapsed < self.CAUSATION_WINDOW_MS:
            # Navigation happened shortly after a click/type — it was caused by it
            self._interaction_caused_navigation = True
            return False
        return True

# Usage in recorder:
# page.on("framenavigated") → check tracker.is_user_initiated_navigation()
# Only record as ActionType.NAVIGATE if True
```

**Additional heuristic:** Track the actual URL before and after click actions. If a click's target is an anchor (`<a href="...">`) or a form submit, the subsequent navigation is definitely click-caused.

---

## CNL (Constraint Natural Language) System

### What is CNL?

A structured, human-readable language for describing test steps. Users write CNL in the Web UI, and it's saved alongside the recorded test. During execution, CNL descriptions serve as **semantic queries** for element matching.

### CNL Grammar

```
<action> ::= <click_action> | <type_action> | <navigate_action> | <select_action>
                | <hover_action> | <wait_action> | <assert_action>

<click_action>    ::= 'Click' <element_ref> ';'
<type_action>     ::= 'Type' <quoted_text> 'in' <element_ref> ';'
<navigate_action> ::= 'Navigate to' <quoted_text> ';'
<select_action>   ::= 'Select' <quoted_text> 'in' <element_ref> ';'
<hover_action>    ::= 'Hover' <element_ref> ';'
<wait_action>     ::= 'Wait' <number> 'seconds' ';'
<assert_action>   ::= 'Assert' <element_ref> <assertion> ';'

<element_ref>     ::= <quoted_text> <element_type>?
<element_type>    ::= 'Button' | 'Link' | 'TextField' | 'Checkbox' | 'Dropdown'
                    | 'Radio' | 'Tab' | 'Menu' | 'Icon' | 'Image' | 'Text'

<assertion>       ::= 'is visible' | 'is hidden' | 'contains' <quoted_text>
                    | 'has value' <quoted_text>

<quoted_text>     ::= '"' <any_text> '"'
```

### CNL Examples

```
Click "Log in" Button;
Type "admin@example.com" in "Enter Username" TextField;
Type "password123" in "Enter Password" TextField;
Click "Submit" Button;
Assert "Welcome, Admin" Text is visible;
Navigate to "https://example.com/dashboard";
Select "Premium" in "Plan Type" Dropdown;
Click "Save Changes" Button;
```

### CNL Storage in RecordedTest

```json
{
    "id": "abc-123",
    "name": "Login Test",
    "cnl": "Click \"Log in\" Button;\nType \"admin@example.com\" in \"Enter Username\" TextField;\nType \"password123\" in \"Enter Password\" TextField;\nClick \"Submit\" Button;",
    "cnl_steps": [
        {
            "step_number": 1,
            "raw_cnl": "Click \"Log in\" Button;",
            "action_type": "click",
            "element_query": "Log in Button",
            "value": null,
            "element_type_hint": "Button"
        },
        {
            "step_number": 2,
            "raw_cnl": "Type \"admin@example.com\" in \"Enter Username\" TextField;",
            "action_type": "type",
            "element_query": "Enter Username TextField",
            "value": "admin@example.com",
            "element_type_hint": "TextField"
        }
    ],
    "actions": [ ... ]
}
```

### CNL as Semantic Query for Element Matching

During execution, the CNL step's `element_query` (e.g., `"Log in Button"`) is used as an **additional semantic query** for the embedding strategy:

```
Query construction priority:
1. CNL element_query (if available) — e.g., "Log in Button"
2. Recorded selector attributes — tag, text, aria-label, etc.
3. Combined: "Log in Button | button#login-btn.submit | aria-label:Log in"

The CNL query is the MOST human-meaningful description → best for embeddings.
```

### CNL Web UI Page

A dedicated page in the Web UI where users can:
1. **Write CNL** from scratch to create a new test
2. **View auto-generated CNL** from a recorded test (recorder generates CNL descriptions)
3. **Edit CNL** for existing recorded tests
4. **Parse & validate** CNL syntax in real-time
5. **Map CNL steps** to recorded actions (1:1 correspondence)

---

## Auto-Healing System

### Concept

When the executor uses a **fallback strategy** (embedding or VLM) to find an element — meaning the original selectors failed — the test file is automatically updated with the new, working selectors. This way, next execution uses direct selectors again.

### Auto-Heal Flow

```
Step execution:
    1. SelectorStrategy tries recorded selectors → FAILS
    2. EmbeddingStrategy finds element (similarity: 0.92) → SUCCEEDS
    3. Action is performed successfully
    4. AUTO-HEAL TRIGGERS:
        a. Extract new selectors from the found element:
           - New CSS selector
           - New XPath
           - Updated ID, class, name, aria-label, text, etc.
           - New outer_html_snippet
        b. Update the RecordedAction in memory:
           - action.selector = new SelectorInfo(...)
           - action.selector.previous_selectors = [old selectors]  # Keep history
           - action.healed = True
           - action.healed_by = "embedding"
           - action.healed_at = timestamp
           - action.heal_similarity = 0.92
        c. Write updated test JSON to disk (atomic write)
        d. Log: "Step 3 auto-healed: button#old-id → button#new-id (embedding, 0.92)"

Same for VLM fallback:
    3. VLMStrategy finds element → SUCCEEDS
    4. AUTO-HEAL with healed_by = "vlm"
```

### Auto-Heal Data Model

```python
class HealRecord(BaseModel):
    healed_at: datetime
    healed_by: ResolutionMethod          # "embedding" | "vlm"
    similarity_score: float | None       # For embedding
    previous_selector: SelectorInfo      # What we had before
    new_selector: SelectorInfo           # What works now

class RecordedAction(BaseModel):
    # ... existing fields ...
    heal_history: list[HealRecord] = []  # Track all heals over time
    last_healed: datetime | None = None
```

### Auto-Heal Safety Rules

1. **Only heal on successful action** — if the element was found but the action failed (e.g., element not interactable), do NOT heal
2. **Minimum similarity threshold for healing** — only heal from embedding if similarity ≥ 0.85 (same as match threshold)
3. **Atomic file writes** — write to temp file, then rename, to prevent corruption
4. **Heal history** — keep previous selectors in `heal_history` array for audit trail
5. **Configurable** — auto-heal can be disabled via config (`executor.auto_heal = false`)

---

## Project Structure

```
SAT/
├── pyproject.toml                     # Project config, dependencies, entry points
├── config/
│   └── default.toml                   # Default configuration
├── recordings/                        # Default directory for saved test recordings
├── sat/
│   ├── __init__.py
│   ├── cli.py                         # Typer CLI entry point
│   ├── config.py                      # Config loader (TOML)
│   ├── constants.py                   # Shared constants
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── playwright_manager.py      # Playwright browser lifecycle (event-driven)
│   │   ├── browser_factory.py         # Chromium/Firefox launch config
│   │   └── models.py                  # Pydantic data models (actions, tests, reports)
│   │
│   ├── recorder/
│   │   ├── __init__.py
│   │   ├── recorder.py                # Main recorder orchestrator
│   │   ├── event_listener.py          # CDP event subscription via expose_function
│   │   ├── action_builder.py          # Raw events → RecordedAction models
│   │   ├── selector_extractor.py      # Extracts robust selectors from elements
│   │   ├── navigation_tracker.py      # User-initiated vs click-caused navigation
│   │   ├── dom_snapshot.py            # Captures interactable elements HTML
│   │   └── cnl_generator.py           # Auto-generates CNL from recorded actions
│   │
│   ├── executor/
│   │   ├── __init__.py
│   │   ├── executor.py                # Main executor orchestrator
│   │   ├── strategy_chain.py          # Fallback strategy pipeline
│   │   ├── strategies/
│   │   │   ├── __init__.py
│   │   │   ├── base.py                # Abstract strategy interface
│   │   │   ├── selector_strategy.py   # Strategy 1: direct selector/locator
│   │   │   ├── embedding_strategy.py  # Strategy 2: Ollama embeddings + cosine sim
│   │   │   └── vlm_strategy.py        # Strategy 3: Ollama VLM vision fallback
│   │   ├── action_performer.py        # Executes action on resolved element
│   │   ├── auto_healer.py             # Updates selectors after fallback success
│   │   └── report.py                  # Execution report generator
│   │
│   ├── cnl/
│   │   ├── __init__.py
│   │   ├── parser.py                  # CNL grammar parser
│   │   ├── validator.py               # CNL syntax validation
│   │   ├── models.py                  # CNL step data models
│   │   └── generator.py              # Generate CNL from RecordedAction list
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── ollama_embedding.py        # Ollama embedding API wrapper
│   │   ├── ollama_vlm.py             # Ollama VLM (vision) API wrapper
│   │   └── dom_parser.py             # HTML → interactable elements extractor (JS)
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── test_store.py             # CRUD for recorded test JSON files
│   │   └── schemas.py                # JSON schema validation
│   │
│   └── web/
│       ├── __init__.py
│       ├── app.py                     # FastAPI application
│       ├── routes/
│       │   ├── __init__.py
│       │   ├── recordings.py          # CRUD endpoints for recordings
│       │   ├── executor.py            # Run/stop test execution endpoints
│       │   ├── cnl.py                 # CNL editor endpoints (parse, validate, save)
│       │   └── ws.py                  # WebSocket for live recording/execution feed
│       ├── static/                    # Frontend assets (JS, CSS)
│       └── templates/                 # Jinja2 templates
│
├── tests/
│   ├── unit/
│   │   ├── test_cnl_parser.py
│   │   ├── test_selector_extractor.py
│   │   ├── test_navigation_tracker.py
│   │   ├── test_auto_healer.py
│   │   └── test_embedding_strategy.py
│   ├── integration/
│   │   ├── test_recorder.py
│   │   ├── test_executor.py
│   │   └── test_strategy_chain.py
│   └── fixtures/
│       ├── sample_recording.json
│       └── sample_cnl.txt
│
└── README.md
```

---

## Data Models (Pydantic)

```python
# === Selector & Element Models ===

class SelectorInfo(BaseModel):
    css: str | None = None
    xpath: str | None = None
    id: str | None = None
    name: str | None = None
    class_name: str | None = None
    tag_name: str
    text_content: str | None = None
    aria_label: str | None = None
    placeholder: str | None = None
    data_testid: str | None = None
    href: str | None = None
    role: str | None = None
    # For embedding context:
    outer_html_snippet: str              # Truncated outerHTML (max ~500 chars)
    parent_html_snippet: str | None = None

class ActionType(str, Enum):
    CLICK = "click"
    TYPE = "type"
    NAVIGATE = "navigate"             # User-initiated URL change ONLY
    NEW_TAB = "new_tab"
    SWITCH_TAB = "switch_tab"
    CLOSE_TAB = "close_tab"
    SCROLL = "scroll"
    SELECT = "select"
    HOVER = "hover"

# === Heal Tracking ===

class HealRecord(BaseModel):
    healed_at: datetime
    healed_by: str                       # "embedding" | "vlm"
    similarity_score: float | None = None
    previous_selector: SelectorInfo
    new_selector: SelectorInfo

# === Recorded Action ===

class RecordedAction(BaseModel):
    step_number: int
    timestamp: datetime
    action_type: ActionType
    url: str
    tab_id: str
    selector: SelectorInfo | None = None  # None for navigate/tab actions
    value: str | None = None              # Typed text, URL, selected value
    screenshot_path: str | None = None
    dom_snapshot_path: str | None = None
    viewport: dict                        # {width, height, scroll_x, scroll_y}
    element_position: dict | None = None  # {x, y, width, height}
    metadata: dict = {}                   # Key modifiers, etc.
    # CNL
    cnl_step: str | None = None           # e.g., 'Click "Log in" Button;'
    # Auto-heal
    heal_history: list[HealRecord] = []
    last_healed: datetime | None = None

# === CNL Step ===

class CNLStep(BaseModel):
    step_number: int
    raw_cnl: str                          # 'Click "Log in" Button;'
    action_type: ActionType
    element_query: str                    # "Log in Button" — used as embedding query
    value: str | None = None              # For type/select actions
    element_type_hint: str | None = None  # "Button", "TextField", etc.

# === Recorded Test ===

class RecordedTest(BaseModel):
    id: str                               # UUID
    name: str
    description: str = ""
    created_at: datetime
    start_url: str
    browser: str                          # "chromium" | "firefox"
    actions: list[RecordedAction]
    tags: list[str] = []
    # CNL
    cnl: str | None = None                # Full CNL text
    cnl_steps: list[CNLStep] = []         # Parsed CNL steps

# === Execution Results ===

class StepResult(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"

class ResolutionMethod(str, Enum):
    SELECTOR = "selector"
    EMBEDDING = "embedding"
    VLM = "vlm"
    NONE = "none"

class ExecutionStepResult(BaseModel):
    step_number: int
    action: RecordedAction
    result: StepResult
    resolution_method: ResolutionMethod | None = None
    similarity_score: float | None = None
    error: str | None = None
    duration_ms: int
    screenshot_path: str | None = None
    healed: bool = False                  # Was auto-heal triggered?

class ExecutionReport(BaseModel):
    test_id: str
    test_name: str
    executed_at: datetime
    total_steps: int
    passed: int
    failed: int
    skipped: int
    duration_s: float
    steps: list[ExecutionStepResult]
    healed_steps: int = 0                 # Count of auto-healed steps
```

---

## Configuration

```toml
# config/default.toml

[browser]
type = "chromium"                     # "chromium" | "firefox"
headless = false
viewport_width = 1920
viewport_height = 1080

[recorder]
output_dir = "./recordings"
capture_screenshots = true
screenshot_format = "png"
debounce_click_ms = 200
debounce_typing_ms = 500
capture_dom_snapshot = true
auto_generate_cnl = true              # Auto-generate CNL from recorded actions
navigation_causation_window_ms = 2000 # Window to detect click-caused navigation

[executor]
timeout_per_step_s = 10
strategies = ["selector", "embedding", "vlm"]
auto_heal = true                      # Enable auto-healing of selectors

[executor.selector]
timeout_ms = 5000

[executor.embedding]
model = "nomic-embed-text"            # Ollama embedding model
min_cosine_similarity = 0.85
max_candidates = 50
ollama_base_url = "http://localhost:11434"

[executor.vlm]
model = "llava:13b"
ollama_base_url = "http://localhost:11434"
temperature = 0.1
max_tokens = 1024

[web]
host = "0.0.0.0"
port = 8000
```

---

## Phase 1 — Project Scaffolding & Core Infrastructure

### 1.1 Setup
- `pyproject.toml` with all dependencies and CLI entry point
- Config loader (TOML-based, merges defaults with user overrides)
- Pydantic models for all data structures
- Playwright browser factory (Chromium + Firefox)

### 1.2 Playwright Manager (Event-Driven)
- Launch browser with Playwright async API
- All communication over CDP WebSocket (zero polling)
- Expose lifecycle: `start()`, `stop()`, `get_page()`, `get_context()`

### 1.3 Dependencies

```toml
[project]
dependencies = [
    "playwright>=1.44",               # Core automation (CDP WebSocket, event-driven)
    "pydantic>=2.0",                  # Data models & validation
    "typer>=0.12",                    # CLI framework
    "rich>=13.0",                     # Terminal output formatting
    "ollama>=0.3",                    # Ollama client (embeddings + VLM)
    "numpy>=1.26",                    # Cosine similarity math
    "fastapi>=0.110",                 # Web UI backend
    "uvicorn>=0.30",                  # ASGI server
    "websockets>=12.0",              # WebSocket support for live feed
    "jinja2>=3.1",                    # Templating for web UI
    "Pillow>=10.0",                   # Screenshot processing for VLM
    "toml>=0.10",                     # Config parsing
]
```

---

## Phase 2 — Recorder (Event-Driven Capture)

### 2.1 Event Listener (CDP Events via Playwright)

All events are **push-based** — the browser notifies SAT, SAT never polls.

```python
# Event setup — all use page.expose_function() or page.on() (CDP WebSocket)

class EventListener:
    async def setup(self, page: Page):
        # Expose Python handlers to the browser (CDP channel)
        await page.expose_function("__sat_click", self._on_click)
        await page.expose_function("__sat_input", self._on_input)

        # Inject capture scripts (runs in every frame, including new ones)
        await page.add_init_script(path="sat/recorder/capture.js")

        # Playwright native events (all event-driven, no polling)
        page.on("framenavigated", self._on_navigate)
        page.on("close", self._on_tab_close)

        # New tab detection (on the browser context)
        page.context.on("page", self._on_new_tab)

    async def _on_click(self, event_data: dict):
        """Called instantly by browser via CDP when user clicks."""
        # event_data contains: selector, tag, text, coordinates, attributes
        # → Build RecordedAction

    async def _on_input(self, event_data: dict):
        """Called by browser via CDP when user types."""
        # Debounced in JS — sends final value after pause

    async def _on_navigate(self, frame):
        """Called by Playwright when navigation occurs."""
        # Check NavigationCausationTracker — only record if user-initiated

    async def _on_new_tab(self, page: Page):
        """Called instantly when a new tab opens."""
        # Attach event listeners to the new page too
        await self.setup(page)

    async def _on_tab_close(self, page: Page):
        """Called instantly when a tab closes."""
```

### 2.2 capture.js — Injected Browser Script

```javascript
// Minimal script injected via page.add_init_script()
// Uses window.__sat_click / __sat_input exposed by Playwright

(function() {
    // Click capture
    document.addEventListener('click', async (e) => {
        const el = e.target;
        await window.__sat_click({
            tag: el.tagName,
            id: el.id || null,
            className: el.className || null,
            name: el.getAttribute('name'),
            text: el.textContent?.trim()?.substring(0, 200),
            ariaLabel: el.getAttribute('aria-label'),
            placeholder: el.getAttribute('placeholder'),
            dataTestId: el.getAttribute('data-test-id'),
            href: el.getAttribute('href'),
            role: el.getAttribute('role'),
            outerHTML: el.outerHTML.substring(0, 500),
            parentHTML: el.parentElement?.outerHTML?.substring(0, 300),
            css: computeUniqueSelector(el),
            xpath: computeXPath(el),
            x: e.clientX,
            y: e.clientY,
            rect: el.getBoundingClientRect().toJSON(),
            viewport: { width: window.innerWidth, height: window.innerHeight,
                        scrollX: window.scrollX, scrollY: window.scrollY }
        });
    }, true);  // Capture phase for earliest detection

    // Input capture (debounced)
    let inputTimeout = null;
    document.addEventListener('input', (e) => {
        clearTimeout(inputTimeout);
        const el = e.target;
        inputTimeout = setTimeout(async () => {
            await window.__sat_input({
                tag: el.tagName,
                // ... same attribute extraction as click ...
                value: el.value,
            });
        }, 500);  // 500ms debounce
    }, true);

    function computeUniqueSelector(el) { /* CSS selector generator */ }
    function computeXPath(el) { /* XPath generator */ }
})();
```

### 2.3 Navigation Causation Tracker

```python
class NavigationCausationTracker:
    """Determines if navigation was user-initiated (URL bar) or action-caused."""

    def __init__(self, causation_window_ms: int = 2000):
        self._window_ms = causation_window_ms
        self._last_interaction_time: float = 0
        self._pending_click_urls: set[str] = set()  # URLs from <a> clicks

    def on_user_interaction(self, action_type: str, target_href: str | None = None):
        self._last_interaction_time = time.time()
        if target_href:
            self._pending_click_urls.add(target_href)

    def is_user_initiated_navigation(self, new_url: str) -> bool:
        elapsed_ms = (time.time() - self._last_interaction_time) * 1000

        # If this URL was clicked as a link → definitely not user-initiated
        if new_url in self._pending_click_urls:
            self._pending_click_urls.discard(new_url)
            return False

        # If within causation window after any interaction → likely caused by it
        if elapsed_ms < self._window_ms:
            return False

        return True  # User typed URL or used back/forward
```

### 2.4 CNL Auto-Generator

The recorder automatically generates CNL for each action:

```python
class CNLGenerator:
    def generate(self, action: RecordedAction) -> str:
        match action.action_type:
            case ActionType.CLICK:
                label = self._get_label(action.selector)
                el_type = self._get_element_type(action.selector)
                return f'Click "{label}" {el_type};'
            case ActionType.TYPE:
                label = self._get_label(action.selector)
                el_type = self._get_element_type(action.selector)
                return f'Type "{action.value}" in "{label}" {el_type};'
            case ActionType.NAVIGATE:
                return f'Navigate to "{action.value}";'
            case ActionType.NEW_TAB:
                return f'Open new tab "{action.value}";'
            case ActionType.SWITCH_TAB:
                return f'Switch to tab "{action.value}";'

    def _get_label(self, selector: SelectorInfo) -> str:
        """Best human label: aria-label > text > placeholder > name > id."""
        return (selector.aria_label or selector.text_content
                or selector.placeholder or selector.name
                or selector.id or selector.css or "element")

    def _get_element_type(self, selector: SelectorInfo) -> str:
        """Map tag_name + role to CNL element type."""
        tag = selector.tag_name.lower()
        role = (selector.role or "").lower()
        if tag == "button" or role == "button": return "Button"
        if tag == "a" or role == "link": return "Link"
        if tag == "input":
            input_type = selector.metadata.get("type", "text")
            if input_type in ("text", "email", "password", "search"):
                return "TextField"
            if input_type == "checkbox": return "Checkbox"
            if input_type == "radio": return "Radio"
        if tag == "select" or role == "listbox": return "Dropdown"
        if tag == "textarea": return "TextField"
        return "Element"
```

---

## Phase 3 — Executor (Intelligent Replay)

### 3.1 Strategy Chain

```python
class StrategyChain:
    def __init__(self, strategies: list[ResolutionStrategy]):
        self.strategies = strategies  # [SelectorStrategy, EmbeddingStrategy, VLMStrategy]

    async def resolve_element(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, ResolutionMethod, float | None]:
        for strategy in self.strategies:
            element, score = await strategy.resolve(page, action)
            if element is not None:
                return element, strategy.method, score
        return None, ResolutionMethod.NONE, None
```

### 3.2 Strategy 1 — Selector Strategy (Direct Locators)

Playwright locators with auto-wait (event-driven, no polling):

```python
class SelectorStrategy(ResolutionStrategy):
    method = ResolutionMethod.SELECTOR

    async def resolve(self, page: Page, action: RecordedAction):
        selector = action.selector
        if not selector:
            return None, None

        locator_attempts = [
            # Priority order — most stable first
            (f"[data-testid='{selector.data_testid}']" if selector.data_testid else None),
            (f"#{selector.id}" if selector.id else None),
            (f"[name='{selector.name}']" if selector.name else None),
            (page.get_by_role(self._map_role(selector), name=selector.text_content)
                if selector.text_content and selector.role else None),
            (page.get_by_text(selector.text_content, exact=True)
                if selector.text_content else None),
            (selector.css if selector.css else None),
            (f"xpath={selector.xpath}" if selector.xpath else None),
        ]

        for locator_str in locator_attempts:
            if locator_str is None:
                continue
            try:
                locator = (locator_str if isinstance(locator_str, Locator)
                          else page.locator(locator_str))
                # Playwright auto-waits — event-driven, no polling
                await locator.wait_for(state="visible", timeout=self._timeout_ms)
                if await locator.count() == 1:
                    return await locator.element_handle(), None
            except (TimeoutError, Error):
                continue
        return None, None
```

### 3.3 Strategy 2 — Embedding Strategy (Ollama)

```python
class EmbeddingStrategy(ResolutionStrategy):
    method = ResolutionMethod.EMBEDDING

    def __init__(self, config):
        self._model = config.executor.embedding.model  # "nomic-embed-text"
        self._min_similarity = config.executor.embedding.min_cosine_similarity  # 0.85
        self._max_candidates = config.executor.embedding.max_candidates
        self._client = ollama.AsyncClient(host=config.executor.embedding.ollama_base_url)

    async def resolve(self, page: Page, action: RecordedAction):
        # 1. Build query from CNL (preferred) or selector attributes
        query = self._build_query(action)

        # 2. Extract interactable elements from current DOM
        candidates = await self._extract_candidates(page)  # JS injection
        if not candidates:
            return None, None

        # 3. Embed query + all candidates via Ollama
        texts = [query] + [c["html"] for c in candidates[:self._max_candidates]]
        embeddings = await self._batch_embed(texts)

        query_embedding = embeddings[0]
        candidate_embeddings = embeddings[1:]

        # 4. Cosine similarity
        similarities = cosine_similarity(query_embedding, candidate_embeddings)
        best_idx = np.argmax(similarities)
        best_score = similarities[best_idx]

        if best_score >= self._min_similarity:
            # Resolve the DOM element by its index
            element = await self._get_element_by_index(page, candidates[best_idx]["index"])
            return element, float(best_score)

        return None, None

    def _build_query(self, action: RecordedAction) -> str:
        """Build semantic query from CNL or selector info."""
        parts = []

        # CNL is the best semantic signal
        if action.cnl_step:
            parts.append(action.cnl_step)

        if action.selector:
            s = action.selector
            if s.text_content: parts.append(s.text_content)
            if s.aria_label: parts.append(s.aria_label)
            if s.placeholder: parts.append(s.placeholder)
            if s.tag_name: parts.append(s.tag_name)
            if s.role: parts.append(s.role)
            if s.id: parts.append(s.id)
            if s.class_name: parts.append(s.class_name)
            if s.outer_html_snippet: parts.append(s.outer_html_snippet)

        return " | ".join(parts)

    async def _batch_embed(self, texts: list[str]) -> list[np.ndarray]:
        """Embed all texts via Ollama in one batch (or sequential if batch unsupported)."""
        embeddings = []
        for text in texts:
            response = await self._client.embeddings(
                model=self._model,
                prompt=text
            )
            embeddings.append(np.array(response["embedding"]))
        return embeddings
        # NOTE: Ollama may support batch in future; adapt when available
        # For speed: use asyncio.gather with semaphore for parallel requests
```

### 3.4 Strategy 3 — VLM Strategy (Ollama)

```python
class VLMStrategy(ResolutionStrategy):
    method = ResolutionMethod.VLM

    async def resolve(self, page: Page, action: RecordedAction):
        # 1. Screenshot current page
        screenshot = await page.screenshot(type="png")

        # 2. Build VLM prompt
        prompt = self._build_prompt(action)

        # 3. Send to Ollama VLM
        response = await self._client.chat(
            model=self._config.executor.vlm.model,
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(screenshot).decode()]
            }],
            options={
                "temperature": self._config.executor.vlm.temperature,
                "num_predict": self._config.executor.vlm.max_tokens,
            }
        )

        # 4. Parse response — extract coordinates
        coords = self._parse_coordinates(response["message"]["content"])
        if not coords:
            return None, None

        # 5. Get element at coordinates
        element = await page.evaluate_handle(
            f"document.elementFromPoint({coords['x']}, {coords['y']})"
        )
        if element:
            return element.as_element(), None
        return None, None

    def _build_prompt(self, action: RecordedAction) -> str:
        cnl = action.cnl_step or ""
        selector_desc = ""
        if action.selector:
            s = action.selector
            selector_desc = (f"tag={s.tag_name}, text='{s.text_content}', "
                           f"aria-label='{s.aria_label}', class='{s.class_name}'")

        return f"""I need to find a UI element on this webpage.

Action to perform: {action.action_type.value}
CNL description: {cnl}
Original element: {selector_desc}
Original position: {action.element_position}

Look at the screenshot and identify the exact element.
Return ONLY a JSON object: {{"x": <number>, "y": <number>, "description": "<what you found>"}}
If no matching element exists, return: {{"found": false}}"""
```

### 3.5 Auto-Healer

```python
class AutoHealer:
    def __init__(self, test_store: TestStore, enabled: bool = True):
        self._store = test_store
        self._enabled = enabled

    async def heal(
        self,
        page: Page,
        action: RecordedAction,
        element: ElementHandle,
        method: ResolutionMethod,
        similarity_score: float | None,
        test: RecordedTest,
    ) -> bool:
        """Update the action's selectors with the newly found element's selectors."""
        if not self._enabled or method == ResolutionMethod.SELECTOR:
            return False  # No healing needed for direct selector match

        # Extract new selectors from the found element
        new_selector_data = await page.evaluate("""
            (el) => ({
                tag: el.tagName,
                id: el.id || null,
                className: el.className || null,
                name: el.getAttribute('name'),
                text: el.textContent?.trim()?.substring(0, 200),
                ariaLabel: el.getAttribute('aria-label'),
                placeholder: el.getAttribute('placeholder'),
                dataTestId: el.getAttribute('data-test-id'),
                href: el.getAttribute('href'),
                role: el.getAttribute('role'),
                outerHTML: el.outerHTML.substring(0, 500),
                parentHTML: el.parentElement?.outerHTML?.substring(0, 300) || null,
                css: computeUniqueSelector(el),
                xpath: computeXPath(el),
            })
        """, element)

        new_selector = SelectorInfo(**self._map_to_selector(new_selector_data))

        # Record heal history
        heal_record = HealRecord(
            healed_at=datetime.utcnow(),
            healed_by=method.value,
            similarity_score=similarity_score,
            previous_selector=action.selector,
            new_selector=new_selector,
        )
        action.heal_history.append(heal_record)
        action.selector = new_selector
        action.last_healed = datetime.utcnow()

        # Persist atomically
        await self._store.save_test_atomic(test)
        return True
```

### 3.6 Executor Orchestrator (Event-Driven)

```python
class Executor:
    async def execute(self, test: RecordedTest) -> ExecutionReport:
        page = await self._browser_manager.new_page()

        # Navigate to start URL — event-driven wait
        await page.goto(test.start_url, wait_until="networkidle")

        results = []
        healed_count = 0

        for action in test.actions:
            start = time.monotonic()

            if action.action_type == ActionType.NAVIGATE:
                await page.goto(action.value, wait_until="networkidle")
                results.append(ExecutionStepResult(
                    step_number=action.step_number, action=action,
                    result=StepResult.PASSED, resolution_method=None,
                    duration_ms=int((time.monotonic() - start) * 1000)
                ))
                continue

            if action.action_type in (ActionType.NEW_TAB, ActionType.SWITCH_TAB,
                                       ActionType.CLOSE_TAB):
                await self._handle_tab_action(page, action)
                # ... result ...
                continue

            # Resolve element via strategy chain
            element, method, score = await self.strategy_chain.resolve_element(page, action)

            if element is None:
                results.append(ExecutionStepResult(
                    step_number=action.step_number, action=action,
                    result=StepResult.FAILED,
                    resolution_method=ResolutionMethod.NONE,
                    error="Could not find element with any strategy",
                    duration_ms=int((time.monotonic() - start) * 1000)
                ))
                continue

            # Perform the action
            try:
                await self.action_performer.perform(page, element, action)
                # Playwright auto-waits for action completion (event-driven)
                await page.wait_for_load_state("networkidle")
            except Exception as e:
                results.append(ExecutionStepResult(
                    step_number=action.step_number, action=action,
                    result=StepResult.FAILED, resolution_method=method,
                    error=str(e),
                    duration_ms=int((time.monotonic() - start) * 1000)
                ))
                continue

            # Auto-heal if needed
            healed = await self.auto_healer.heal(
                page, action, element, method, score, test
            )
            if healed:
                healed_count += 1

            results.append(ExecutionStepResult(
                step_number=action.step_number, action=action,
                result=StepResult.PASSED, resolution_method=method,
                similarity_score=score, healed=healed,
                duration_ms=int((time.monotonic() - start) * 1000)
            ))

        return ExecutionReport(
            test_id=test.id, test_name=test.name,
            executed_at=datetime.utcnow(),
            total_steps=len(results),
            passed=sum(1 for r in results if r.result == StepResult.PASSED),
            failed=sum(1 for r in results if r.result == StepResult.FAILED),
            skipped=sum(1 for r in results if r.result == StepResult.SKIPPED),
            duration_s=sum(r.duration_ms for r in results) / 1000,
            steps=results,
            healed_steps=healed_count,
        )
```

---

## Phase 4 — Speed Optimizations (Fastest Execution Focus)

### 4.1 Strategy-Level Speed

| Optimization | Detail |
|---|---|
| **Playwright auto-wait** | No explicit waits or sleeps — Playwright waits for exactly the right condition via CDP events |
| **Selector strategy first** | Direct locator lookup is <10ms — try all selectors before any ML |
| **Parallel embedding** | Use `asyncio.gather()` with semaphore for Ollama embedding calls |
| **Lazy model loading** | Embedding model loaded only when selector strategy fails for the first time |
| **Pre-warm Ollama** | `sat doctor` command warms up models; executor can pre-warm on startup |
| **DOM extraction once** | Extract interactable elements once per step, cache for both embedding and VLM |
| **Skip VLM if embedding confident** | If embedding score ≥ 0.95, skip VLM entirely even if configured |
| **`networkidle` tuning** | Configurable: use `"domcontentloaded"` instead of `"networkidle"` for faster pages |

### 4.2 Execution-Level Speed

| Optimization | Detail |
|---|---|
| **Connection reuse** | Single browser instance, single Ollama connection for entire test |
| **No unnecessary screenshots** | Only capture screenshots when VLM strategy is triggered |
| **Batch DOM operations** | Single `page.evaluate()` call extracts all interactable elements + attributes |
| **Async I/O everywhere** | All file writes, Ollama calls, browser commands are async |
| **Result streaming** | WebSocket streams results to UI as they happen — don't wait for full report |

### 4.3 Ollama Embedding Speed

```python
# Parallel embedding with connection pooling
async def _batch_embed_parallel(self, texts: list[str]) -> list[np.ndarray]:
    """Embed texts in parallel batches via Ollama."""
    semaphore = asyncio.Semaphore(8)  # Limit concurrent Ollama requests

    async def embed_one(text: str):
        async with semaphore:
            response = await self._client.embeddings(model=self._model, prompt=text)
            return np.array(response["embedding"])

    return await asyncio.gather(*[embed_one(t) for t in texts])
```

---

## Phase 5 — CLI Interface

```bash
# Record a new test
sat record --url "https://example.com" --name "Login Test" --browser chromium

# Execute a recorded test
sat execute recordings/abc123/test.json --browser chromium

# Execute with specific strategies only
sat execute recordings/abc123/test.json --strategies selector,embedding

# Execute with auto-heal disabled
sat execute recordings/abc123/test.json --no-auto-heal

# List all recordings
sat list

# Show recording details + CNL
sat show abc123

# Edit CNL for a recording
sat cnl edit abc123

# Validate CNL syntax
sat cnl validate "Click \"Log in\" Button;"

# Start the web UI
sat web --port 8000

# Check system health (Ollama models, browser, etc.)
sat doctor
```

---

## Phase 6 — Web UI

### Pages

1. **Dashboard** — List all recordings, status, tags, last execution result
2. **Record** — Start/stop recording, live action feed via WebSocket
3. **CNL Editor** — Write/edit CNL for tests, real-time syntax validation, auto-suggest
4. **Execute** — Run tests, live progress via WebSocket, step-by-step results
5. **Report** — View execution report with screenshots, pass/fail, heal indicators

### WebSocket Live Feed

```python
# Both recording and execution stream events in real-time

@router.websocket("/ws/record")
async def ws_record(websocket: WebSocket):
    await websocket.accept()
    recorder = get_recorder()
    recorder.on_action(lambda action: websocket.send_json(action.dict()))
    # Browser events push to recorder → recorder pushes to WebSocket → UI updates

@router.websocket("/ws/execute/{test_id}")
async def ws_execute(websocket: WebSocket, test_id: str):
    await websocket.accept()
    executor = get_executor()
    executor.on_step_complete(lambda result: websocket.send_json(result.dict()))
    # Each step completion pushes instantly to UI
```

---

## Phase 7 — Implementation Sprints

| Sprint | Deliverable | Est. Effort |
|---|---|---|
| **1** | Project scaffold, pyproject.toml, config, Pydantic models, browser factory | 1 day |
| **2** | Playwright event listener, capture.js, expose_function setup | 2 days |
| **3** | Selector extractor, navigation tracker, action builder | 1.5 days |
| **4** | Recorder orchestrator, JSON serialization, CNL auto-generator | 1 day |
| **5** | CNL parser, validator, data models | 1 day |
| **6** | Executor scaffold, selector strategy (Playwright locators) | 1 day |
| **7** | Ollama embedding service, embedding strategy, cosine similarity | 2 days |
| **8** | Ollama VLM service, VLM strategy, coordinate-based fallback | 2 days |
| **9** | Strategy chain integration, action performer, auto-healer | 1.5 days |
| **10** | CLI interface (Typer), all commands | 1 day |
| **11** | FastAPI backend, REST endpoints, WebSocket live feed | 2 days |
| **12** | Web UI frontend (dashboard, record, CNL editor, execute, report) | 2.5 days |
| **13** | Tests (unit + integration), `sat doctor`, README, polish | 2 days |

**Total estimated: ~20.5 days**

---

## Key Design Decisions & Edge Cases

1. **Event-driven everywhere** — Playwright CDP WebSocket = browser notifies agent. No polling at any layer.
2. **Navigation detection** — Causation tracker + `<a>` href tracking distinguishes user URL changes from click-caused navigations
3. **CNL as primary query** — During execution, CNL description is the strongest semantic signal for embedding matching
4. **Auto-healing** — Selectors updated in-place after fallback success; heal history preserved for audit
5. **Ollama for everything** — Single service for embeddings (`nomic-embed-text`) and VLM (`llava:13b`). Unified stack, no external API costs.
6. **Speed-first** — Playwright auto-wait, parallel Ollama calls, lazy model loading, minimal screenshots
7. **iframes** — `page.add_init_script()` runs in all frames automatically; Playwright's locator API pierces iframes
8. **Shadow DOM** — Playwright locators penetrate shadow DOM by default
9. **Tab matching** — Playwright gives each tab its own `Page` object; match by URL/title pattern
10. **Atomic saves** — Test files written atomically (write temp → rename) to prevent corruption during auto-heal
