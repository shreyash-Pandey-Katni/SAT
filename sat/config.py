"""Configuration loader for SAT.

Merges config/default.toml with an optional user-supplied config file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import toml

from sat.constants import DEFAULT_CONFIG_PATH


# ---------------------------------------------------------------------------
# Sub-config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BrowserConfig:
    type: str = "chromium"
    headless: bool = False
    viewport_width: int = 1920
    viewport_height: int = 1080
    slow_mo: int = 0
    # Optional: explicit path to Chrome/Chromium/Firefox binary.
    # When empty and headless=False, auto-detection of system Chrome is used
    # (Linux, macOS, and Windows).
    executable_path: str = ""


@dataclass
class RecorderConfig:
    output_dir: str = "./recordings"
    capture_screenshots: bool = True
    screenshot_format: str = "png"
    max_reports_per_test: int = 50
    debounce_click_ms: int = 200
    debounce_typing_ms: int = 500
    capture_dom_snapshot: bool = True
    auto_generate_cnl: bool = True
    navigation_causation_window_ms: int = 2000


@dataclass
class SelectorStrategyConfig:
    timeout_ms: int = 5000


@dataclass
class EmbeddingStrategyConfig:
    model: str = "nomic-embed-text"
    min_cosine_similarity: float = 0.85
    max_candidates: int = 50
    concurrency: int = 8
    ollama_base_url: str = "http://localhost:11434"


@dataclass
class VLMStrategyConfig:
    model: str = "llava:7b"
    ollama_base_url: str = "http://localhost:11434"
    temperature: float = 0.1
    max_tokens: int = 1024


@dataclass
class OCRStrategyConfig:
    min_confidence: float = 0.80
    min_match_score: float = 0.85
    languages: list[str] = field(default_factory=lambda: ["en"])
    gpu: bool = False


@dataclass
class ExecutorConfig:
    timeout_per_step_s: int = 10
    strategies: list[str] = field(
        default_factory=lambda: ["selector", "embedding", "ocr", "vlm"]
    )
    auto_heal: bool = True
    wait_after_action: str = "networkidle"
    selector: SelectorStrategyConfig = field(default_factory=SelectorStrategyConfig)
    embedding: EmbeddingStrategyConfig = field(default_factory=EmbeddingStrategyConfig)
    ocr: OCRStrategyConfig = field(default_factory=OCRStrategyConfig)
    vlm: VLMStrategyConfig = field(default_factory=VLMStrategyConfig)


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class VariablesConfig:
    global_file: str = "config/variables.toml"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_file: str = "logs/sat.log"
    max_bytes: int = 5_242_880       # 5 MB per file before rotation
    backup_count: int = 5            # number of rotated backups to keep


@dataclass
class SATConfig:
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    executor: ExecutorConfig = field(default_factory=ExecutorConfig)
    web: WebConfig = field(default_factory=WebConfig)
    variables: VariablesConfig = field(default_factory=VariablesConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(user_config_path: str | Path | None = None) -> SATConfig:
    """Load SAT configuration.

    Args:
        user_config_path: Optional path to a user TOML config.  Values in the
            user file override the defaults.
    """
    with open(DEFAULT_CONFIG_PATH) as fh:
        data = toml.load(fh)

    if user_config_path is not None:
        with open(user_config_path) as fh:
            user_data = toml.load(fh)
        data = _deep_merge(data, user_data)

    return _dict_to_config(data)


def _dict_to_config(data: dict[str, Any]) -> SATConfig:
    b = data.get("browser", {})
    r = data.get("recorder", {})
    e = data.get("executor", {})
    w = data.get("web", {})
    v = data.get("variables", {})
    lg = data.get("logging", {})

    return SATConfig(
        browser=BrowserConfig(
            type=b.get("type", "chromium"),
            headless=b.get("headless", False),
            viewport_width=b.get("viewport_width", 1920),
            viewport_height=b.get("viewport_height", 1080),
            slow_mo=b.get("slow_mo", 0),
            executable_path=b.get("executable_path", ""),
        ),
        recorder=RecorderConfig(
            output_dir=r.get("output_dir", "./recordings"),
            capture_screenshots=r.get("capture_screenshots", True),
            screenshot_format=r.get("screenshot_format", "png"),
            max_reports_per_test=r.get("max_reports_per_test", 50),
            debounce_click_ms=r.get("debounce_click_ms", 200),
            debounce_typing_ms=r.get("debounce_typing_ms", 500),
            capture_dom_snapshot=r.get("capture_dom_snapshot", True),
            auto_generate_cnl=r.get("auto_generate_cnl", True),
            navigation_causation_window_ms=r.get("navigation_causation_window_ms", 2000),
        ),
        executor=ExecutorConfig(
            timeout_per_step_s=e.get("timeout_per_step_s", 10),
            strategies=e.get("strategies", ["selector", "embedding", "ocr", "vlm"]),
            auto_heal=e.get("auto_heal", True),
            wait_after_action=e.get("wait_after_action", "networkidle"),
            selector=SelectorStrategyConfig(**e.get("selector", {})),
            embedding=EmbeddingStrategyConfig(**e.get("embedding", {})),
            ocr=OCRStrategyConfig(**e.get("ocr", {})),
            vlm=VLMStrategyConfig(**e.get("vlm", {})),
        ),
        web=WebConfig(
            host=w.get("host", "0.0.0.0"),
            port=w.get("port", 8000),
        ),
        variables=VariablesConfig(
            global_file=v.get("global_file", "config/variables.toml"),
        ),
        logging=LoggingConfig(
            level=lg.get("level", "INFO"),
            log_file=lg.get("log_file", "logs/sat.log"),
            max_bytes=lg.get("max_bytes", 5_242_880),
            backup_count=lg.get("backup_count", 5),
        ),
    )
