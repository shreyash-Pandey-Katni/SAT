"""ExecutionReport generator — builds and saves execution reports."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sat.core.models import ExecutionReport, ExecutionStepResult, StepResult


class ReportGenerator:
    """Builds :class:`ExecutionReport` objects and saves them to disk."""

    def build(
        self,
        test_id: str,
        test_name: str,
        steps: list[ExecutionStepResult],
        started_at: datetime,
    ) -> ExecutionReport:
        ended_at = datetime.utcnow()
        duration_s = (ended_at - started_at).total_seconds()

        passed = sum(1 for s in steps if s.result == StepResult.PASSED)
        failed = sum(1 for s in steps if s.result == StepResult.FAILED)
        skipped = sum(1 for s in steps if s.result == StepResult.SKIPPED)
        healed = sum(1 for s in steps if s.healed)

        return ExecutionReport(
            id=str(uuid.uuid4()),
            test_id=test_id,
            test_name=test_name,
            executed_at=started_at,
            total_steps=len(steps),
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_s=round(duration_s, 3),
            steps=steps,
            healed_steps=healed,
        )

    def save(self, report: ExecutionReport, reports_dir: Path) -> Path:
        """Persist the report as JSON and return the path."""
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / f"{report.id}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return path
