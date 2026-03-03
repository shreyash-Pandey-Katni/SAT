"""Executor API routes (trigger execution, get latest report, parallel execution)."""

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


class ParallelExecuteRequest(BaseModel):
    test_ids: List[str]
    max_workers: int = 4
    browser: Optional[str] = None
    strategies: Optional[List[str]] = None


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


@router.post("/execute-parallel")
async def execute_parallel(body: ParallelExecuteRequest, request: Request):
    """Execute multiple tests in parallel and return all reports."""
    from sat.executor.parallel_executor import ParallelExecutor

    base_cfg = request.app.state.cfg
    cfg = copy.deepcopy(base_cfg)

    if body.browser:
        cfg.browser.type = body.browser
    if body.strategies:
        cfg.executor.strategies = body.strategies

    store = TestStore(
        cfg.recorder.output_dir,
        max_reports_per_test=cfg.recorder.max_reports_per_test,
    )

    tests = []
    not_found = []
    for tid in body.test_ids:
        try:
            tests.append(store.get_test(tid))
        except FileNotFoundError:
            not_found.append(tid)

    if not tests:
        raise HTTPException(status_code=404, detail="No valid tests found")

    pe = ParallelExecutor(cfg, max_workers=body.max_workers)
    reports = await pe.execute_all(tests)

    for r in reports:
        store.save_report(r)

    return JSONResponse(content={
        "total_tests": len(reports),
        "total_passed": sum(r.passed for r in reports),
        "total_failed": sum(r.failed for r in reports),
        "not_found": not_found,
        "reports": [r.model_dump(mode="json") for r in reports],
    })
