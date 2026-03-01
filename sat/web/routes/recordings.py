"""Recordings CRUD API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from sat.storage.test_store import TestStore

router = APIRouter()


def _store(request: Request) -> TestStore:
    return TestStore(request.app.state.cfg.recorder.output_dir)


@router.get("")
async def list_recordings(request: Request):
    store = _store(request)
    tests = store.list_tests()
    return [
        {
            "id": t.id,
            "name": t.name,
            "browser": t.browser,
            "steps": len(t.actions),
            "created_at": t.created_at.isoformat(),
            "has_cnl": bool(t.cnl),
        }
        for t in tests
    ]


@router.get("/{test_id}")
async def get_recording(test_id: str, request: Request):
    store = _store(request)
    try:
        test = store.get_test(test_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Test not found")
    return JSONResponse(content=test.model_dump(mode="json"))


@router.delete("/{test_id}")
async def delete_recording(test_id: str, request: Request):
    store = _store(request)
    try:
        store.delete_test(test_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Test not found")
    return {"deleted": test_id}


@router.get("/{test_id}/reports")
async def list_reports(test_id: str, request: Request):
    store = _store(request)
    reports = store.list_reports(test_id)
    return [
        {
            "id": r.id,
            "test_id": r.test_id,
            "passed": r.passed,
            "failed": r.failed,
            "healed_steps": r.healed_steps,
            "duration_s": r.duration_s,
            "executed_at": r.executed_at.isoformat(),
        }
        for r in reports
    ]


@router.get("/{test_id}/reports/{report_id}")
async def get_report(test_id: str, report_id: str, request: Request):
    store = _store(request)
    try:
        report = store.get_report(test_id, report_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Report not found")
    return JSONResponse(content=report.model_dump(mode="json"))
