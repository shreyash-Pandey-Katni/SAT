"""Recordings CRUD API routes."""

from __future__ import annotations

from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from sat.core.models import ExecutionReport

from sat.storage.test_store import TestStore

router = APIRouter()


def _build_comparison(
    current: ExecutionReport,
    baseline: ExecutionReport,
) -> dict:
    current_steps = {step.step_number: step for step in current.steps}
    baseline_steps = {step.step_number: step for step in baseline.steps}
    all_step_numbers = sorted(set(current_steps) | set(baseline_steps))

    step_diffs = []
    improved = 0
    regressed = 0
    unchanged = 0

    for step_number in all_step_numbers:
        cur = current_steps.get(step_number)
        base = baseline_steps.get(step_number)

        if cur and base:
            from_result = base.result.value
            to_result = cur.result.value
            changed = from_result != to_result

            if from_result == "failed" and to_result == "passed":
                delta_type = "improved"
                improved += 1
            elif from_result == "passed" and to_result == "failed":
                delta_type = "regressed"
                regressed += 1
            elif changed:
                delta_type = "changed"
            else:
                delta_type = "unchanged"
                unchanged += 1

            step_diffs.append(
                {
                    "step_number": step_number,
                    "delta_type": delta_type,
                    "changed": changed,
                    "from_result": from_result,
                    "to_result": to_result,
                    "from_strategy": base.resolution_method.value if base.resolution_method else "none",
                    "to_strategy": cur.resolution_method.value if cur.resolution_method else "none",
                    "from_duration_ms": base.duration_ms,
                    "to_duration_ms": cur.duration_ms,
                    "duration_delta_ms": cur.duration_ms - base.duration_ms,
                    "from_healed": base.healed,
                    "to_healed": cur.healed,
                    "cnl_step": cur.cnl_step or base.cnl_step,
                }
            )
        elif cur and not base:
            step_diffs.append(
                {
                    "step_number": step_number,
                    "delta_type": "added",
                    "changed": True,
                    "from_result": None,
                    "to_result": cur.result.value,
                    "from_strategy": None,
                    "to_strategy": cur.resolution_method.value if cur.resolution_method else "none",
                    "from_duration_ms": None,
                    "to_duration_ms": cur.duration_ms,
                    "duration_delta_ms": None,
                    "from_healed": None,
                    "to_healed": cur.healed,
                    "cnl_step": cur.cnl_step,
                }
            )
        elif base and not cur:
            step_diffs.append(
                {
                    "step_number": step_number,
                    "delta_type": "removed",
                    "changed": True,
                    "from_result": base.result.value,
                    "to_result": None,
                    "from_strategy": base.resolution_method.value if base.resolution_method else "none",
                    "to_strategy": None,
                    "from_duration_ms": base.duration_ms,
                    "to_duration_ms": None,
                    "duration_delta_ms": None,
                    "from_healed": base.healed,
                    "to_healed": None,
                    "cnl_step": base.cnl_step,
                }
            )

    return {
        "current_report": {
            "id": current.id,
            "executed_at": current.executed_at.isoformat(),
            "status": current.status.value,
            "passed": current.passed,
            "failed": current.failed,
            "healed_steps": current.healed_steps,
        },
        "baseline_report": {
            "id": baseline.id,
            "executed_at": baseline.executed_at.isoformat(),
            "status": baseline.status.value,
            "passed": baseline.passed,
            "failed": baseline.failed,
            "healed_steps": baseline.healed_steps,
        },
        "summary": {
            "total_steps_compared": len(all_step_numbers),
            "improved_steps": improved,
            "regressed_steps": regressed,
            "unchanged_steps": unchanged,
            "duration_delta_s": round(current.duration_s - baseline.duration_s, 3),
        },
        "steps": step_diffs,
    }


def _build_junit_xml(report: ExecutionReport) -> str:
    tests = report.total_steps
    failures = report.failed
    skipped = report.skipped

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<testsuite name="{escape(report.test_name)}" '
            f'tests="{tests}" failures="{failures}" skipped="{skipped}" '
            f'time="{report.duration_s:.3f}" timestamp="{report.executed_at.isoformat()}">'
        ),
    ]

    for step in report.steps:
        action_type = step.action.action_type.value
        case_name = f"step_{step.step_number}_{action_type}"
        lines.append(
            (
                f'<testcase classname="{escape(report.test_name)}" '
                f'name="{escape(case_name)}" time="{step.duration_ms / 1000:.3f}">'
            )
        )
        if step.result.value == "failed":
            message = escape(step.error or "Step failed")
            lines.append(f'<failure message="{message}">{message}</failure>')
        elif step.result.value == "skipped":
            lines.append("<skipped />")

        info_parts = [
            f"result={step.result.value}",
            f"strategy={(step.resolution_method.value if step.resolution_method else 'none')}",
            f"healed={step.healed}",
        ]
        if step.cnl_step:
            info_parts.append(f"cnl={step.cnl_step}")
        lines.append(f"<system-out>{escape(' | '.join(info_parts))}</system-out>")
        lines.append("</testcase>")

    lines.append("</testsuite>")
    return "\n".join(lines)


def _store(request: Request) -> TestStore:
    cfg = request.app.state.cfg
    return TestStore(
        cfg.recorder.output_dir,
        max_reports_per_test=cfg.recorder.max_reports_per_test,
    )


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
            "test_name": r.test_name,
            "status": r.status,
            "total_steps": r.total_steps,
            "passed": r.passed,
            "failed": r.failed,
            "skipped": r.skipped,
            "healed_steps": r.healed_steps,
            "duration_s": r.duration_s,
            "start_url": r.start_url,
            "resolution_summary": r.resolution_summary,
            "executed_at": r.executed_at.isoformat(),
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
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


@router.get("/{test_id}/reports/{report_id}/compare/{baseline_report_id}")
async def compare_reports(
    test_id: str,
    report_id: str,
    baseline_report_id: str,
    request: Request,
):
    store = _store(request)
    try:
        current = store.get_report(test_id, report_id)
        baseline = store.get_report(test_id, baseline_report_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Report not found")

    comparison = _build_comparison(current=current, baseline=baseline)
    return JSONResponse(content=comparison)


@router.get("/{test_id}/reports/{report_id}/export/junit.xml")
async def export_report_junit(test_id: str, report_id: str, request: Request):
    store = _store(request)
    try:
        report = store.get_report(test_id, report_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Report not found")

    xml_content = _build_junit_xml(report)
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{report.test_id}_{report.id}.junit.xml"'
        },
    )
