"""CNL management routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from sat.cnl.validator import validate_cnl
from sat.storage.test_store import TestStore

router = APIRouter()


class CNLUpdateBody(BaseModel):
    cnl: str


class CNLValidateBody(BaseModel):
    cnl: str


@router.get("/recordings/{test_id}/cnl")
async def get_cnl(test_id: str, request: Request):
    store = TestStore(request.app.state.cfg.recorder.output_dir)
    try:
        test = store.get_test(test_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Test not found")
    return {"cnl": test.cnl or "", "steps": [s.model_dump() for s in test.cnl_steps]}


@router.post("/recordings/{test_id}/cnl")
async def update_cnl(test_id: str, body: CNLUpdateBody, request: Request):
    store = TestStore(request.app.state.cfg.recorder.output_dir)
    try:
        test = store.update_cnl(test_id, body.cnl)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Test not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"steps_parsed": len(test.cnl_steps)}


@router.post("/cnl/validate")
async def validate(body: CNLValidateBody):
    errors = validate_cnl(body.cnl)
    if errors:
        return {"valid": False, "errors": [{"line": e.line, "message": e.message} for e in errors]}
    return {"valid": True, "errors": []}
