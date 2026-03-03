"""Variable substitution for CNL text.

Supports ``${var_name}`` placeholders in CNL text, resolved from:
1. A **global** variables TOML file (``config/variables.toml``).
2. A **per-test** variables TOML file (stored alongside test.json).
3. Runtime overrides passed as a plain ``dict[str, str]``.

Merge order (last wins): global → per-test → runtime overrides.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import toml

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)}")


# ---------------------------------------------------------------------------
# VariableContext — holds merged variables, supports store-at-runtime
# ---------------------------------------------------------------------------


class VariableContext:
    """Holds variable values and supports runtime mutation (via STORE)."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._vars: dict[str, str] = dict(initial or {})

    # -- read --
    def get(self, name: str) -> str | None:
        return self._vars.get(name)

    def get_all(self) -> dict[str, str]:
        return dict(self._vars)

    # -- write (for STORE action) --
    def set(self, name: str, value: str) -> None:
        self._vars[name] = value

    # -- substitute placeholders in a string --
    def substitute(self, text: str) -> str:
        """Replace ``${var}`` tokens in *text* with their values.

        Unknown variables are left as-is so the user sees clear errors.
        """
        return _substitute(text, self._vars)


# ---------------------------------------------------------------------------
# Pure-function helpers (usable without a VariableContext instance)
# ---------------------------------------------------------------------------


def load_variables(
    global_path: str | Path | None = None,
    per_test_path: str | Path | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Load and merge variable sources.  Returns a flat ``{name: value}`` map."""
    merged: dict[str, str] = {}

    for path in (global_path, per_test_path):
        if path is not None:
            p = Path(path)
            if p.is_file():
                data = toml.load(p)
                merged.update(_flatten(data))

    if overrides:
        merged.update(overrides)

    return merged


def substitute(text: str, variables: dict[str, str]) -> str:
    """Replace all ``${var}`` tokens in *text*."""
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


def has_variables(text: str) -> bool:
    """Return *True* if *text* contains any ``${…}`` placeholders."""
    return bool(_VAR_RE.search(text))


def extract_variable_names(text: str) -> list[str]:
    """Return deduplicated variable names found in *text*."""
    seen: set[str] = set()
    names: list[str] = []
    for m in _VAR_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _substitute(text: str, variables: dict[str, str]) -> str:
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten nested TOML sections into ``section_key`` names.

    Top-level keys are used as-is; nested tables use ``parent_child``.
    All values are stringified.
    """
    result: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}_{key}"
        if isinstance(value, dict):
            result.update(_flatten(value, full_key))
        else:
            result[full_key if prefix else key] = str(value)
    return result
