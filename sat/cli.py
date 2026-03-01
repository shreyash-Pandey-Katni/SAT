"""SAT CLI — Typer-based command-line interface."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

app = typer.Typer(
    name="sat",
    help="SAT — Activity Recorder & Intelligent Executor",
    add_completion=False,
)
cnl_app = typer.Typer(help="CNL management commands")
app.add_typer(cnl_app, name="cnl")

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: Optional[str]):
    from sat.config import load_config
    return load_config(config_path)


def _get_store(config):
    from sat.storage.test_store import TestStore
    return TestStore(config.recorder.output_dir)


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

@app.command()
def record(
    url: str = typer.Argument(..., help="Starting URL to record from"),
    name: str = typer.Option(..., "--name", "-n", help="Name for this test recording"),
    description: str = typer.Option("", "--desc", "-d", help="Optional description"),
    tags: Optional[str] = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
    browser: Optional[str] = typer.Option(None, "--browser", "-b", help="chromium or firefox"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Record a new test by interacting with a browser."""
    from sat.config import load_config
    from sat.recorder.recorder import Recorder

    cfg = load_config(config_path)
    if browser:
        cfg.browser.type = browser

    recorder = Recorder(cfg)
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    async def _run():
        loop = asyncio.get_running_loop()

        def _graceful_stop():
            console.print("\n[yellow]Stopping recorder — finishing up...[/yellow]")
            recorder.stop()

        # Use asyncio-native signal handler: does NOT raise KeyboardInterrupt
        # into the event loop, so in-flight awaits complete cleanly.
        loop.add_signal_handler(signal.SIGINT, _graceful_stop)
        loop.add_signal_handler(signal.SIGTERM, _graceful_stop)
        try:
            test = await recorder.record(url, name=name, description=description, tags=tag_list)
            console.print(f"\n[green]Recording saved[/green]  id=[bold]{test.id}[/bold]  steps={len(test.actions)}")
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
                loop.remove_signal_handler(signal.SIGTERM)
            except Exception:
                pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

@app.command()
def execute(
    test_path: str = typer.Argument(..., help="Path to test.json or test ID"),
    browser: Optional[str] = typer.Option(None, "--browser", "-b"),
    strategies: Optional[str] = typer.Option(None, "--strategies", "-s",
        help="Comma-separated strategy order, e.g. selector,embedding,vlm"),
    no_auto_heal: bool = typer.Option(False, "--no-auto-heal"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Execute a recorded test with the intelligent fallback chain."""
    from sat.config import load_config
    from sat.core.models import RecordedTest
    from sat.executor.executor import Executor
    from sat.storage.test_store import TestStore
    import json

    cfg = load_config(config_path)
    if browser:
        cfg.browser.type = browser
    if strategies:
        cfg.executor.strategies = [s.strip() for s in strategies.split(",")]
    if no_auto_heal:
        cfg.executor.auto_heal = False

    # Resolve test path
    p = Path(test_path)
    if p.is_file():
        data = json.loads(p.read_text())
        test = RecordedTest.model_validate(data)
    else:
        # Treat as test_id
        store = TestStore(cfg.recorder.output_dir)
        test = store.get_test(test_path)

    async def _on_step(result):
        icon = "✓" if result.result.value == "passed" else "✗"
        color = "green" if result.result.value == "passed" else "red"
        heal_tag = " [yellow](healed)[/yellow]" if result.healed else ""
        method = result.resolution_method.value if result.resolution_method else "—"
        console.print(
            f"  [{color}]{icon}[/{color}] step {result.step_number:>3}  "
            f"{result.action.action_type.value:<12}  [{method}]{heal_tag}  "
            f"{result.duration_ms}ms"
        )

    async def _run():
        executor = Executor(cfg)
        report = await executor.execute(test)
        console.print(
            f"\n[bold]Results:[/bold]  passed=[green]{report.passed}[/green]  "
            f"failed=[red]{report.failed}[/red]  healed=[yellow]{report.healed_steps}[/yellow]  "
            f"duration={report.duration_s:.2f}s"
        )
        return report.failed

    failed = asyncio.run(_run())
    if failed > 0:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@app.command("list")
def list_tests(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """List all recorded tests."""
    cfg = _load_config(config_path)
    store = _get_store(cfg)
    tests = store.list_tests()

    if not tests:
        console.print("[yellow]No recordings found.[/yellow]")
        return

    table = Table(title="Recorded Tests")
    table.add_column("ID", style="cyan", no_wrap=True, max_width=12)
    table.add_column("Name")
    table.add_column("Steps", justify="right")
    table.add_column("Browser")
    table.add_column("Created")
    table.add_column("CNL", justify="center")

    for t in tests:
        has_cnl = "✓" if t.cnl else "—"
        table.add_row(
            t.id[:8],
            t.name,
            str(len(t.actions)),
            t.browser,
            t.created_at.strftime("%Y-%m-%d %H:%M"),
            has_cnl,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@app.command()
def show(
    test_id: str = typer.Argument(..., help="Test ID (full or prefix)"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Show details of a recorded test including CNL."""
    cfg = _load_config(config_path)
    store = _get_store(cfg)
    test = store.get_test(test_id)

    console.print(f"\n[bold]Test:[/bold] {test.name}  [{test.id}]")
    console.print(f"[bold]URL:[/bold] {test.start_url}")
    console.print(f"[bold]Browser:[/bold] {test.browser}")
    console.print(f"[bold]Steps:[/bold] {len(test.actions)}")
    if test.cnl:
        console.print(f"\n[bold cyan]CNL:[/bold cyan]\n{test.cnl}")

    table = Table(title="Actions")
    table.add_column("#", justify="right")
    table.add_column("Type")
    table.add_column("Selector / URL")
    table.add_column("CNL", max_width=50)

    for a in test.actions:
        sel = ""
        if a.selector:
            sel = a.selector.css or a.selector.id or a.selector.text_content or ""
        elif a.value:
            sel = a.value
        table.add_row(str(a.step_number), a.action_type.value, sel[:60], a.cnl_step or "")

    console.print(table)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@app.command()
def delete(
    test_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Delete a recorded test and all its artifacts."""
    if not yes:
        typer.confirm(f"Delete test {test_id!r}?", abort=True)
    cfg = _load_config(config_path)
    store = _get_store(cfg)
    store.delete_test(test_id)
    console.print(f"[green]Deleted[/green] {test_id}")


# ---------------------------------------------------------------------------
# cnl commands
# ---------------------------------------------------------------------------

@cnl_app.command("validate")
def cnl_validate(
    cnl_text: str = typer.Argument(..., help="CNL text to validate"),
) -> None:
    """Validate a CNL string."""
    from sat.cnl.validator import validate_cnl
    errors = validate_cnl(cnl_text)
    if not errors:
        console.print("[green]✓ CNL is valid[/green]")
    else:
        for e in errors:
            console.print(f"[red]Line {e.line}: {e.message}[/red]")
        raise typer.Exit(code=1)


@cnl_app.command("update")
def cnl_update(
    test_id: str = typer.Argument(...),
    cnl_file: str = typer.Argument(..., help="Path to .cnl or .txt file"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Update the CNL for a recorded test from a file."""
    cfg = _load_config(config_path)
    store = _get_store(cfg)
    cnl_text = Path(cnl_file).read_text(encoding="utf-8")
    test = store.update_cnl(test_id, cnl_text)
    console.print(f"[green]CNL updated[/green] — {len(test.cnl_steps)} steps parsed")


# ---------------------------------------------------------------------------
# web
# ---------------------------------------------------------------------------

@app.command()
def web(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port", "-p"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Start the SAT web UI."""
    import uvicorn
    from sat.web.app import create_app

    cfg = _load_config(config_path)
    fast_app = create_app(cfg)
    uvicorn.run(fast_app, host=host, port=port)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@app.command()
def doctor(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Check system health (Playwright, Ollama models, etc.)."""
    from sat.services.ollama_embedding import OllamaEmbeddingService
    from sat.services.ollama_vlm import OllamaVLMService

    cfg = _load_config(config_path)

    async def _check():
        console.print("[bold]SAT Doctor[/bold]\n")

        # Playwright
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                await browser.close()
            console.print("[green]✓[/green] Playwright (chromium)")
        except Exception as e:
            console.print(f"[red]✗[/red] Playwright: {e}")

        # Embedding model
        emb = OllamaEmbeddingService(
            model=cfg.executor.embedding.model,
            base_url=cfg.executor.embedding.ollama_base_url,
        )
        ok = await emb.health_check()
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"{icon} Ollama embedding ({cfg.executor.embedding.model})")

        # VLM model
        vlm = OllamaVLMService(
            model=cfg.executor.vlm.model,
            base_url=cfg.executor.vlm.ollama_base_url,
        )
        ok = await vlm.health_check()
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"{icon} Ollama VLM ({cfg.executor.vlm.model})")

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
