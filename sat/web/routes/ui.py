"""UI (HTML) routes — serve Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


def _t(request: Request):
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _t(request).TemplateResponse("dashboard.html", {"request": request})


@router.get("/record", response_class=HTMLResponse)
async def record_page(request: Request):
    return _t(request).TemplateResponse("record.html", {"request": request})


@router.get("/tests/{test_id}", response_class=HTMLResponse)
async def test_detail(test_id: str, request: Request):
    return _t(request).TemplateResponse("test_detail.html", {"request": request, "test_id": test_id})


@router.get("/tests/{test_id}/cnl", response_class=HTMLResponse)
async def cnl_editor(test_id: str, request: Request):
    return _t(request).TemplateResponse("cnl_editor.html", {"request": request, "test_id": test_id})


@router.get("/tests/{test_id}/execute", response_class=HTMLResponse)
async def execute_page(test_id: str, request: Request):
    return _t(request).TemplateResponse("execute.html", {"request": request, "test_id": test_id})


@router.get("/tests/{test_id}/reports/{report_id}", response_class=HTMLResponse)
async def report_page(test_id: str, report_id: str, request: Request):
    return _t(request).TemplateResponse(
        "report.html", {"request": request, "test_id": test_id, "report_id": report_id}
    )
