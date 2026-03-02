"""TestStore — CRUD operations for recorded test JSON files."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sat.core.models import ExecutionReport, RecordedTest


def _safe_replace(src: str, dst: str | Path, retries: int = 3) -> None:
    """os.replace with retry for Windows (file may be held by antivirus/indexer)."""
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform == "win32" and attempt < retries - 1:
                time.sleep(0.1 * (attempt + 1))
            else:
                raise


def _to_utc_timestamp(value: datetime) -> float:
    """Normalize naive/aware datetimes to a UTC timestamp for stable sorting."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.timestamp()


class TestStore:
    """Manages the recordings directory structure.

    Layout::

        recordings/
            <test_id>/
                test.json           ← RecordedTest
                screenshots/        ← recorder screenshots
                dom_snapshots/      ← DOM snapshot JSONs
                reports/
                    <report_id>.json
                exec_screenshots/   ← executor screenshots
    """

    def __init__(
        self,
        recordings_dir: str | Path,
        max_reports_per_test: int | None = None,
    ) -> None:
        self._root = Path(recordings_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_reports_per_test = max_reports_per_test

    # ------------------------------------------------------------------
    # RecordedTest CRUD
    # ------------------------------------------------------------------

    def list_tests(self) -> list[RecordedTest]:
        """Return all tests sorted by creation time (newest first)."""
        tests: list[RecordedTest] = []
        for test_dir in self._root.iterdir():
            if not test_dir.is_dir():
                continue
            test_file = test_dir / "test.json"
            if test_file.exists():
                try:
                    tests.append(self._load_test(test_file))
                except Exception:
                    pass
        tests.sort(key=lambda t: _to_utc_timestamp(t.created_at), reverse=True)
        return tests

    def get_test(self, test_id: str) -> RecordedTest:
        path = self._test_path(test_id)
        if not path.exists():
            raise FileNotFoundError(f"Test {test_id!r} not found at {path}")
        return self._load_test(path)

    def save_test(self, test: RecordedTest) -> Path:
        path = self._test_path(test.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(test.model_dump_json(indent=2), encoding="utf-8")
        return path

    def save_test_atomic(self, test: RecordedTest) -> Path:
        """Atomic write: write temp file then rename."""
        path = self._test_path(test.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(test.model_dump_json(indent=2))
            _safe_replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    def delete_test(self, test_id: str) -> None:
        import shutil
        test_dir = self._root / test_id
        if test_dir.exists():
            shutil.rmtree(test_dir)

    # ------------------------------------------------------------------
    # ExecutionReport CRUD
    # ------------------------------------------------------------------

    def list_reports(self, test_id: str) -> list[ExecutionReport]:
        reports_dir = self._root / test_id / "reports"
        if not reports_dir.exists():
            return []
        reports: list[ExecutionReport] = []
        for f in reports_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                reports.append(ExecutionReport.model_validate(data))
            except Exception:
                pass
        reports.sort(key=lambda r: _to_utc_timestamp(r.executed_at), reverse=True)
        return reports

    def get_report(self, test_id: str, report_id: str) -> ExecutionReport:
        path = self._root / test_id / "reports" / f"{report_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Report {report_id!r} not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ExecutionReport.model_validate(data)

    def save_report(self, report: ExecutionReport) -> Path:
        reports_dir = self._root / report.test_id / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / f"{report.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        self._prune_reports(report.test_id)
        return path

    def _prune_reports(self, test_id: str) -> None:
        """Prune old report files based on configured retention."""
        if not self._max_reports_per_test or self._max_reports_per_test <= 0:
            return

        reports_dir = self._root / test_id / "reports"
        if not reports_dir.exists():
            return

        report_files = sorted(
            reports_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old_report in report_files[self._max_reports_per_test:]:
            try:
                old_report.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # CNL update
    # ------------------------------------------------------------------

    def update_cnl(self, test_id: str, cnl_text: str) -> RecordedTest:
        from sat.cnl.parser import parse_cnl

        test = self.get_test(test_id)
        test.cnl = cnl_text
        parsed = parse_cnl(cnl_text)
        test.cnl_steps = parsed.steps

        # Merge CNL steps back into actions
        cnl_by_step = {cs.step_number: cs for cs in parsed.steps}
        for action in test.actions:
            cnl_step = cnl_by_step.get(action.step_number)
            if cnl_step:
                action.cnl_step = cnl_step.raw_cnl

        self.save_test_atomic(test)
        return test

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _test_path(self, test_id: str) -> Path:
        return self._root / test_id / "test.json"

    @staticmethod
    def _load_test(path: Path) -> RecordedTest:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RecordedTest.model_validate(data)
