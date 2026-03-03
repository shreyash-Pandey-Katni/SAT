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

app = typer.Typer(
    name="sat",
    help="SAT — Activity Recorder & Intelligent Executor",
    add_completion=False,
)
cnl_app = typer.Typer(help="CNL management commands")
app.add_typer(cnl_app, name="cnl")

branch_app = typer.Typer(help="Branch management commands")
app.add_typer(branch_app, name="branch")

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: Optional[str]):
    from sat.config import load_config
    return load_config(config_path)


def _get_store(config, branch: str = "main"):
    from sat.storage.test_store import TestStore
    return TestStore(
        config.recorder.output_dir,
        max_reports_per_test=config.recorder.max_reports_per_test,
        branch=branch,
    )


def _setup_logging(config_path: Optional[str] = None) -> None:
    """Load config and initialise rotating-file + console logging."""
    cfg = _load_config(config_path)
    from sat.logging import setup_logging
    setup_logging(
        level=cfg.logging.level,
        log_file=cfg.logging.log_file,
        max_bytes=cfg.logging.max_bytes,
        backup_count=cfg.logging.backup_count,
    )


@app.callback(invoke_without_command=True)
def _main_callback(
    ctx: typer.Context,
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config TOML"),
) -> None:
    """SAT — Activity Recorder & Intelligent Executor."""
    # Store config path for subcommands and set up logging early
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    _setup_logging(config_path)


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
        # Windows ProactorEventLoop doesn't support add_signal_handler,
        # so fall back to signal.signal() there.
        if sys.platform != "win32":
            loop.add_signal_handler(signal.SIGINT, _graceful_stop)
            loop.add_signal_handler(signal.SIGTERM, _graceful_stop)
        else:
            signal.signal(signal.SIGINT, lambda *_: _graceful_stop())
            signal.signal(signal.SIGTERM, lambda *_: _graceful_stop())
        try:
            test = await recorder.record(url, name=name, description=description, tags=tag_list)
            console.print(f"\n[green]Recording saved[/green]  id=[bold]{test.id}[/bold]  steps={len(test.actions)}")
        finally:
            try:
                if sys.platform != "win32":
                    loop.remove_signal_handler(signal.SIGINT)
                    loop.remove_signal_handler(signal.SIGTERM)
                else:
                    signal.signal(signal.SIGINT, signal.SIG_DFL)
                    signal.signal(signal.SIGTERM, signal.SIG_DFL)
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
        store = TestStore(
            cfg.recorder.output_dir,
            max_reports_per_test=cfg.recorder.max_reports_per_test,
        )
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
    host: str = typer.Option("127.0.0.1", "--host"),
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
# branch commands
# ---------------------------------------------------------------------------

@branch_app.command("list")
def branch_list(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """List all branches."""
    cfg = _load_config(config_path)
    store = _get_store(cfg)
    branches = store.list_branches()
    for b in branches:
        console.print(f"  {b}")


@branch_app.command("create")
def branch_create(
    name: str = typer.Argument(..., help="Branch name"),
    copy_from: Optional[str] = typer.Option(None, "--from", help="Copy tests from this branch"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Create a new branch."""
    cfg = _load_config(config_path)
    store = _get_store(cfg)
    store.create_branch(name, copy_from=copy_from)
    console.print(f"[green]Created branch[/green] {name}")


@branch_app.command("delete")
def branch_delete(
    name: str = typer.Argument(..., help="Branch name to delete"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Delete a branch (cannot delete 'main')."""
    if not yes:
        typer.confirm(f"Delete branch {name!r}?", abort=True)
    cfg = _load_config(config_path)
    store = _get_store(cfg)
    try:
        store.delete_branch(name)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted branch[/green] {name}")


# ---------------------------------------------------------------------------
# run-cnl (CLI)
# ---------------------------------------------------------------------------

@app.command("run-cnl")
def run_cnl(
    cnl_file: str = typer.Argument(..., help="Path to .cnl file"),
    start_url: str = typer.Option(..., "--url", "-u", help="Starting URL"),
    name: str = typer.Option("CNL Test", "--name", "-n"),
    vars_file: Optional[str] = typer.Option(None, "--vars", "-v", help="Variables TOML file"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Parse and execute a CNL file against a live browser."""
    from sat.executor.cnl_runner import CNLRunner
    from sat.storage.test_store import TestStore

    cfg = _load_config(config_path)
    cnl_text = Path(cnl_file).read_text(encoding="utf-8")

    # Load per-test variables if provided
    variables: dict[str, str] | None = None
    if vars_file:
        from sat.cnl.variables import load_variables
        variables = load_variables(per_test_path=vars_file)

    async def _on_step(data: dict):
        ok = data.get("status") == "passed"
        icon = "✓" if ok else "✗"
        color = "green" if ok else "red"
        console.print(
            f"  [{color}]{icon}[/{color}] step {data.get('step_number', '?'):>3}  "
            f"{data.get('action_type', ''):<12}  {data.get('cnl_step', '')}"
        )

    async def _run():
        runner = CNLRunner(cfg)
        runner.on_step(_on_step)
        test = await runner.run(cnl_text, start_url, name=name, variables=variables)

        store = TestStore(cfg.recorder.output_dir)
        store.save_test(test)
        console.print(
            f"\n[green]CNL run complete[/green]  id=[bold]{test.id}[/bold]  "
            f"steps={len(test.actions)}"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# execute-parallel
# ---------------------------------------------------------------------------

@app.command("execute-parallel")
def execute_parallel(
    test_ids: str = typer.Argument(..., help="Comma-separated test IDs"),
    max_workers: int = typer.Option(4, "--max-workers", "-w", help="Max parallel browsers"),
    browser: Optional[str] = typer.Option(None, "--browser", "-b"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Execute multiple tests in parallel."""
    from sat.executor.parallel_executor import ParallelExecutor
    from sat.storage.test_store import TestStore

    cfg = _load_config(config_path)
    if browser:
        cfg.browser.type = browser

    store = TestStore(cfg.recorder.output_dir,
                      max_reports_per_test=cfg.recorder.max_reports_per_test)
    ids = [t.strip() for t in test_ids.split(",") if t.strip()]
    tests = []
    for tid in ids:
        try:
            tests.append(store.get_test(tid))
        except FileNotFoundError:
            console.print(f"[red]Test {tid!r} not found — skipping[/red]")

    if not tests:
        console.print("[red]No valid tests to execute.[/red]")
        raise typer.Exit(code=1)

    async def _run():
        pe = ParallelExecutor(cfg, max_workers=max_workers)
        reports = await pe.execute_all(tests)

        total_passed = sum(r.passed for r in reports)
        total_failed = sum(r.failed for r in reports)

        for r in reports:
            icon = "✓" if r.failed == 0 else "✗"
            color = "green" if r.failed == 0 else "red"
            console.print(
                f"  [{color}]{icon}[/{color}] {r.test_name:<30} "
                f"passed={r.passed} failed={r.failed} {r.duration_s:.2f}s"
            )
            store.save_report(r)

        console.print(
            f"\n[bold]Total:[/bold] passed=[green]{total_passed}[/green]  "
            f"failed=[red]{total_failed}[/red]  tests={len(reports)}"
        )
        return total_failed

    failed = asyncio.run(_run())
    if failed > 0:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
