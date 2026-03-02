# SAT — Selenium Activity Tool

Browser activity recorder and intelligent test executor powered by Playwright and Ollama.

## Features

- **Record** browser interactions (click, type, navigate, new-tab, switch-tab, close-tab) via event-driven CDP WebSocket (zero polling)
- **Intelligent executor** — 3-stage fallback chain:
  1. Direct selector (CSS, XPath, id, aria-label, …)
  2. Ollama embeddings cosine similarity ≥ 0.85 (`nomic-embed-text`)
  3. Ollama VLM visual fallback (`llava:13b`)
- **Auto-healing** — when a fallback succeeds, the test JSON is updated atomically with fresh selectors
- **CNL (Constraint Natural Language)** — human-readable step descriptions generated automatically and editable
- **Web UI** — FastAPI + WebSocket for live recording and execution feedback
- **CLI** — Typer-based `sat` command

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com/) running locally
  ```
  ollama pull nomic-embed-text
  ollama pull llava:13b
  ```

## Installation

```bash
git clone <repo-url> && cd SAT

# Create virtual environment
python -m venv .venv && source .venv/bin/activate

# Install project
pip install -e ".[dev]"

# Install Playwright browsers
playwright install chromium firefox
```

## Docker

### Run SAT web UI with Docker Compose (recommended)

```bash
docker compose up --build
```

- SAT web UI: `http://localhost:8000`
- Ollama API (from host): `http://localhost:11434`

Then pull required models into Ollama:

```bash
docker exec -it sat-ollama ollama pull nomic-embed-text
docker exec -it sat-ollama ollama pull llava:13b
```

### Run SAT container only

```bash
docker build -t sat .
docker run --rm -p 8000:8000 -v "$(pwd)/recordings:/app/recordings" sat
```

If you run Ollama outside Compose, update `config/docker.toml` with the correct Ollama base URL.

## CLI Usage

```bash
# Record a new test
sat record https://example.com --name "Login flow" --browser chromium

# List all recordings
sat list

# Show test details
sat show <test-id>

# Execute a test
sat execute <test-id>
sat execute <test-id> --strategies selector,embedding,vlm --no-auto-heal

# Edit/update CNL for a test
sat cnl update <test-id> path/to/steps.cnl

# Validate CNL
sat cnl validate "Click \"Submit\" Button;"

# Start web UI
sat web --port 8000

# System health check
sat doctor
```

## Web UI

```bash
sat web
# Open http://localhost:8000
```

## CNL Syntax

```
# Comment
Navigate to "https://example.com";
Click "Log in" Button;
Type "user@example.com" in "Email" Input;
Type "secret" in "Password" Input;
Click "Submit" Button;
Select "Option A" in "Category" Dropdown;
```

## Configuration

Copy `config/default.toml` and pass it with `--config`:

```bash
sat record https://example.com --name "Test" --config my-config.toml
```

Key settings:
- `[browser]` — `type` (chromium/firefox), `headless`, `viewport`
- `[executor.embedding]` — `model`, `min_cosine_similarity` (default 0.85)
- `[executor.vlm]` — `model` (default `llava:13b`)
- `[recorder]` — `output_dir`, `screenshots`, `debounce_ms`

## Project Structure

```
sat/
  cli.py              # Typer CLI entry point
  config.py           # TOML loader
  constants.py        # Shared constants
  core/
    models.py         # All Pydantic models
    browser_factory.py
    playwright_manager.py
  recorder/           # Event-driven recording
    recorder.py
    event_listener.py
    capture.js        # Browser-side event capture
    action_builder.py
    selector_extractor.py
    navigation_tracker.py
    dom_snapshot.py
    cnl_generator.py
  cnl/                # CNL parsing and validation
    parser.py
    validator.py
    models.py
  executor/           # Intelligent execution
    executor.py
    strategy_chain.py
    action_performer.py
    auto_healer.py
    report.py
    strategies/
      selector_strategy.py
      embedding_strategy.py
      vlm_strategy.py
  services/           # Ollama wrappers
    ollama_embedding.py
    ollama_vlm.py
    dom_parser.py
  storage/
    test_store.py     # JSON-based CRUD
  web/                # FastAPI web UI
    app.py
    routes/
    templates/
    static/
```
