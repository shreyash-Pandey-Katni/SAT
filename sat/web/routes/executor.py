"""Executor API routes (trigger execution, get latest report)."""

from __future__ import annotations

import copy
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List

from sat.storage.test_store import TestStore
from sat.executor.executor import Executor

router = APIRouter()


class ExecuteRequest(BaseModel):
    browser: Optional[str] = None
    strategies: Optional[List[str]] = None
    auto_heal: Optional[bool] = None


@router.post("/execute/{test_id}")
async def execute_test(test_id: str, body: ExecuteRequest, request: Request):
    base_cfg = request.app.state.cfg
    store = TestStore(
        base_cfg.recorder.output_dir,
        max_reports_per_test=base_cfg.recorder.max_reports_per_test,
    )

    try:
        test = store.get_test(test_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Test not found")

    # Apply request overrides
    cfg = copy.deepcopy(base_cfg)

    if body.browser:
        cfg.browser.type = body.browser
    if body.strategies:
        cfg.executor.strategies = body.strategies
    if body.auto_heal is not None:
        cfg.executor.auto_heal = body.auto_heal

    executor = Executor(cfg)
    report = await executor.execute(test)

    # Persist the report
    store.save_report(report)

    return JSONResponse(content=report.model_dump(mode="json"))
