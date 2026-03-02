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
    Streams JSON step results: {"type": "step", "data": {...}} or {"type": "done", ...}
    """
    from sat.config import load_config
    from sat.executor.executor import Executor
    from sat.storage.test_store import TestStore

    await websocket.accept()

    cfg = load_config()
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

    Expects first message: ``{"cnl": "...", "start_url": "...", "name": "..."}``
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
        test = await runner.run(cnl_text, start_url, name=name)

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
