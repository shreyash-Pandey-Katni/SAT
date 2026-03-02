"""ExecutionReport generator — builds and saves execution reports."""

from __future__ import annotations

import platform
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sat import __version__
from sat.config import SATConfig
from sat.core.models import (
    ExecutionReport,
    ExecutionStatus,
    ExecutionStepResult,
    StepResult,
)


class ReportGenerator:
    """Builds :class:`ExecutionReport` objects and saves them to disk."""

    def build(
        self,
        config: SATConfig,
        test_id: str,
        test_name: str,
        start_url: str,
        steps: list[ExecutionStepResult],
        started_at: datetime,
    ) -> ExecutionReport:
        ended_at = datetime.now(UTC)
        duration_s = (ended_at - started_at).total_seconds()

        passed = sum(1 for s in steps if s.result == StepResult.PASSED)
        failed = sum(1 for s in steps if s.result == StepResult.FAILED)
        skipped = sum(1 for s in steps if s.result == StepResult.SKIPPED)
        healed = sum(1 for s in steps if s.healed)

        if failed > 0 and passed > 0:
            status = ExecutionStatus.PARTIAL
        elif failed > 0:
            status = ExecutionStatus.FAILED
        else:
            status = ExecutionStatus.PASSED

        resolution_summary = {
            "selector": sum(1 for s in steps if s.resolution_method and s.resolution_method.value == "selector"),
            "embedding": sum(1 for s in steps if s.resolution_method and s.resolution_method.value == "embedding"),
            "vlm": sum(1 for s in steps if s.resolution_method and s.resolution_method.value == "vlm"),
            "none": sum(1 for s in steps if s.resolution_method and s.resolution_method.value == "none"),
        }

        return ExecutionReport(
            id=str(uuid.uuid4()),
            test_id=test_id,
            test_name=test_name,
            executed_at=started_at,
            ended_at=ended_at,
            status=status,
            start_url=start_url,
            total_steps=len(steps),
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_s=round(duration_s, 3),
            steps=steps,
            healed_steps=healed,
            resolution_summary=resolution_summary,
            environment=ExecutionReport.ExecutionEnvironment(
                browser=config.browser.type,
                headless=config.browser.headless,
                viewport={
                    "width": config.browser.viewport_width,
                    "height": config.browser.viewport_height,
                },
                strategies=list(config.executor.strategies),
                auto_heal=config.executor.auto_heal,
                os=platform.platform(),
                sat_version=__version__,
            ),
        )

    def save(self, report: ExecutionReport, reports_dir: Path) -> Path:
        """Persist the report as JSON and return the path."""
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / f"{report.id}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return path
