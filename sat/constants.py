"""Shared constants for SAT."""

from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "default.toml"
DEFAULT_RECORDINGS_DIR = ROOT_DIR / "recordings"

# Selector priority order (index 0 = highest priority)
SELECTOR_PRIORITY = [
    "data_testid",
    "id",
    "aria_label",
    "name",
    "role_name",
    "text_content",
    "css",
    "xpath",
]

# DOM interactable element selectors (used for embedding candidate extraction)
INTERACTABLE_SELECTORS = (
    "button, a, input, select, textarea, "
    "[role='button'], [role='link'], [role='checkbox'], "
    "[role='radio'], [role='tab'], [role='menuitem'], "
    "[role='option'], [role='switch'], [role='spinbutton'], "
    "[onclick], [tabindex]:not([tabindex='-1'])"
)

# Max HTML snippet length stored in SelectorInfo
OUTER_HTML_MAX_LEN = 500
PARENT_HTML_MAX_LEN = 300

# Screenshot quality
SCREENSHOT_TYPE = "png"

# Recorder debounce
DEFAULT_DEBOUNCE_CLICK_MS = 200
DEFAULT_DEBOUNCE_TYPING_MS = 500

# Navigation causation window
DEFAULT_NAV_CAUSATION_WINDOW_MS = 2000

# Executor
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_MIN_COSINE_SIMILARITY = 0.85
DEFAULT_MAX_CANDIDATES = 50
