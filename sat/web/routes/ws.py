"""WebSocket routes for live recording and execution feeds."""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/record")
async def ws_record(websocket: WebSocket):
    """
    Expects first message: JSON with {"url": "...", "name": "...", "browser": "..."}
    Streams JSON events back: {"type": "action", "data": {...}} or {"type": "done", ...}
    """
    from sat.recorder.recorder import Recorder

    await websocket.accept()

    try:
        init_msg = await websocket.receive_text()
        params = json.loads(init_msg)
    except Exception:
        await websocket.close(code=1008)
        return

    url: str = params.get("url", "")
    name: str = params.get("name", "recording")
    browser: Optional[str] = params.get("browser")

    # Pull config from the app — websocket.app not available directly;
    # we use the application state injected into websocket.scope
    from sat.config import load_config
    cfg = load_config()
    if browser:
        cfg.browser.type = browser

    recorder = Recorder(cfg)
    stop_event: asyncio.Event = asyncio.Event()

    async def _on_action(action):
        try:
            await websocket.send_text(
                json.dumps({"type": "action", "data": action.model_dump(mode="json")})
            )
        except Exception:
            recorder.stop()

    recorder.on_action(_on_action)

    # Listen for "stop" message from client in a background task
    async def _listen_for_stop():
        try:
            while True:
                msg = await websocket.receive_text()
                if json.loads(msg).get("type") == "stop":
                    recorder.stop()
                    break
        except WebSocketDisconnect:
            recorder.stop()

    asyncio.create_task(_listen_for_stop())

    try:
        test = await recorder.record(url, name=name)
        await websocket.send_text(
            json.dumps({"type": "done", "test_id": test.id, "steps": len(test.actions)})
        )
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
    finally:
        await websocket.close()


@router.websocket("/ws/execute/{test_id}")
async def ws_execute(websocket: WebSocket, test_id: str):
    """
    Expects first message: JSON with optional overrides
    ``{"browser": "...", "strategies": [...], "auto_heal": bool}``
    Streams JSON step results: {"type": "step", "data": {...}} or {"type": "done", ...}
    """
    from sat.config import load_config
    from sat.executor.executor import Executor
    from sat.storage.test_store import TestStore

    await websocket.accept()

    # ── Read optional config overrides from the client ────────────────
    try:
        init_msg = await websocket.receive_text()
        params = json.loads(init_msg)
    except Exception:
        params = {}

    cfg = load_config()

    # Apply overrides sent by the Web UI
    if params.get("browser"):
        cfg.browser.type = params["browser"]
    if params.get("strategies"):
        cfg.executor.strategies = params["strategies"]
    if "auto_heal" in params:
        cfg.executor.auto_heal = params["auto_heal"]

    store = TestStore(cfg.recorder.output_dir)

    try:
        test = store.get_test(test_id)
    except FileNotFoundError:
        await websocket.send_text(json.dumps({"type": "error", "message": "Test not found"}))
        await websocket.close()
        return

    executor = Executor(cfg)

    async def _on_step(result):
        try:
            await websocket.send_text(
                json.dumps({"type": "step", "data": result.model_dump(mode="json")})
            )
        except Exception:
            pass

    executor.on_step_complete(_on_step)

    try:
        report = await executor.execute(test)
        store.save_report(report)
        await websocket.send_text(
            json.dumps({"type": "done", "report": report.model_dump(mode="json")})
        )
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
    finally:
        await websocket.close()


@router.websocket("/ws/run-cnl")
async def ws_run_cnl(websocket: WebSocket):
    """Run raw CNL text against a live browser, stream progress, store as test.

    Expects first message: ``{"cnl": "...", "start_url": "...", "name": "...", "variables": {...}}``
    Streams: ``{"type": "step", "data": {...}}`` then ``{"type": "done", ...}``
    """
    from sat.config import load_config
    from sat.executor.cnl_runner import CNLRunner
    from sat.storage.test_store import TestStore

    await websocket.accept()

    try:
        init_msg = await websocket.receive_text()
        params = json.loads(init_msg)
    except Exception:
        await websocket.close(code=1008)
        return

    cnl_text: str = params.get("cnl", "")
    start_url: str = params.get("start_url", "")
    name: str = params.get("name", "CNL Test")
    variables: dict | None = params.get("variables")  # runtime variable overrides

    if not cnl_text or not start_url:
        await websocket.send_text(
            json.dumps({"type": "error", "message": "Both 'cnl' and 'start_url' are required."})
        )
        await websocket.close()
        return

    cfg = load_config()
    runner = CNLRunner(cfg)

    async def _on_step(data: dict):
        try:
            await websocket.send_text(json.dumps({"type": "step", "data": data}))
        except Exception:
            pass

    runner.on_step(_on_step)

    try:
        test = await runner.run(cnl_text, start_url, name=name, variables=variables)

        # Persist the new test
        store = TestStore(cfg.recorder.output_dir)
        store.save_test(test)

        await websocket.send_text(json.dumps({
            "type": "done",
            "test_id": test.id,
            "steps": len(test.actions),
            "name": test.name,
        }))
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
    finally:
        await websocket.close()


@router.websocket("/ws/execute-parallel")
async def ws_execute_parallel(websocket: WebSocket):
    """Execute multiple tests in parallel, streaming live progress.

    Expects first message::

        {"test_ids": [...], "max_workers": 4, "browser": "...", ...}

    Streams ``{"type": "step", ...}`` per step, ``{"type": "test_done", ...}``,
    then ``{"type": "all_done", ...}``.
    """
    from sat.config import load_config
    from sat.executor.parallel_executor import ParallelExecutor
    from sat.storage.test_store import TestStore

    await websocket.accept()

    try:
        init_msg = await websocket.receive_text()
        params = json.loads(init_msg)
    except Exception:
        await websocket.close(code=1008)
        return

    cfg = load_config()
    if params.get("browser"):
        cfg.browser.type = params["browser"]
    if params.get("strategies"):
        cfg.executor.strategies = params["strategies"]

    max_workers = params.get("max_workers", 4)
    test_ids = params.get("test_ids", [])

    store = TestStore(cfg.recorder.output_dir,
                      max_reports_per_test=cfg.recorder.max_reports_per_test)

    tests = []
    for tid in test_ids:
        try:
            tests.append(store.get_test(tid))
        except FileNotFoundError:
            await websocket.send_text(json.dumps({
                "type": "warning",
                "message": f"Test {tid!r} not found — skipping",
            }))

    if not tests:
        await websocket.send_text(json.dumps({
            "type": "error", "message": "No valid tests to execute.",
        }))
        await websocket.close()
        return

    pe = ParallelExecutor(cfg, max_workers=max_workers)

    async def _on_progress(test_id, data):
        try:
            if hasattr(data, "model_dump"):
                payload = {"type": "step", "test_id": test_id,
                           "data": data.model_dump(mode="json")}
            else:
                payload = {"test_id": test_id, **data}
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass

    pe.on_progress(_on_progress)

    try:
        reports = await pe.execute_all(tests)
        for r in reports:
            store.save_report(r)

        await websocket.send_text(json.dumps({
            "type": "all_done",
            "total_tests": len(reports),
            "total_passed": sum(r.passed for r in reports),
            "total_failed": sum(r.failed for r in reports),
            "reports": [{"id": r.id, "test_id": r.test_id, "test_name": r.test_name,
                         "status": r.status.value, "passed": r.passed,
                         "failed": r.failed, "duration_s": r.duration_s}
                        for r in reports],
        }))
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
    finally:
        await websocket.close()
