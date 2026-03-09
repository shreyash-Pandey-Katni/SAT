"""TestStore — CRUD operations for recorded test JSON files.

Supports branch-aware storage:
    recordings/branches/<branch>/<test_id>/test.json
Legacy (no branch) falls back to:
    recordings/<test_id>/test.json
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sat.core.models import ExecutionReport, RecordedTest

if TYPE_CHECKING:
    from sat.config import SATConfig
    from sat.services.cnl_regenerator import RegenerationReport


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
    """Manages the recordings directory structure with branch support.

    Layout::

        recordings/
            branches/
                main/
                    <test_id>/
                        test.json
                        screenshots/
                        dom_snapshots/
                        reports/
                        exec_screenshots/
                experiment/
                    <test_id>/
                        ...
            <test_id>/               ← legacy (no branch)
                test.json
    """

    def __init__(
        self,
        recordings_dir: str | Path,
        max_reports_per_test: int | None = None,
        branch: str = "main",
    ) -> None:
        self._root = Path(recordings_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_reports_per_test = max_reports_per_test
        self._branch = branch

    # ------------------------------------------------------------------
    # Branch management
    # ------------------------------------------------------------------

    @property
    def branch(self) -> str:
        return self._branch

    def set_branch(self, branch: str) -> None:
        self._branch = branch

    def list_branches(self) -> list[str]:
        """Return branch names (always includes 'main')."""
        branches_dir = self._root / "branches"
        names: set[str] = {"main"}
        if branches_dir.exists():
            for d in branches_dir.iterdir():
                if d.is_dir():
                    names.add(d.name)
        return sorted(names)

    def create_branch(self, name: str, copy_from: str | None = None) -> None:
        """Create a new branch.  Optionally copy all tests from *copy_from*."""
        branch_dir = self._root / "branches" / name
        branch_dir.mkdir(parents=True, exist_ok=True)

        if copy_from:
            src = self._root / "branches" / copy_from
            if src.exists():
                for item in src.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, branch_dir / item.name, dirs_exist_ok=True)

    def delete_branch(self, name: str) -> None:
        if name == "main":
            raise ValueError("Cannot delete the 'main' branch")
        branch_dir = self._root / "branches" / name
        if branch_dir.exists():
            shutil.rmtree(branch_dir)

    # ------------------------------------------------------------------
    # Current-branch root helper
    # ------------------------------------------------------------------

    def _branch_root(self, branch: str | None = None) -> Path:
        b = branch or self._branch
        return self._root / "branches" / b

    # ------------------------------------------------------------------
    # RecordedTest CRUD
    # ------------------------------------------------------------------

    def list_tests(self, branch: str | None = None) -> list[RecordedTest]:
        """Return all tests on the current branch, sorted newest first."""
        root = self._branch_root(branch)
        tests: list[RecordedTest] = []

        # Check branch-aware path
        if root.exists():
            for test_dir in root.iterdir():
                if not test_dir.is_dir():
                    continue
                test_file = test_dir / "test.json"
                if test_file.exists():
                    try:
                        tests.append(self._load_test(test_file))
                    except Exception:
                        pass

        # Also include legacy (non-branch) tests for backward compat
        if (branch or self._branch) == "main":
            for test_dir in self._root.iterdir():
                if test_dir.name == "branches" or not test_dir.is_dir():
                    continue
                test_file = test_dir / "test.json"
                if test_file.exists():
                    try:
                        t = self._load_test(test_file)
                        # Avoid duplicates if migrated
                        if not any(existing.id == t.id for existing in tests):
                            tests.append(t)
                    except Exception:
                        pass

        tests.sort(key=lambda t: _to_utc_timestamp(t.created_at), reverse=True)
        return tests

    def get_test(self, test_id: str, branch: str | None = None) -> RecordedTest:
        path = self._test_path(test_id, branch)
        if not path.exists():
            # Fallback: try legacy path
            legacy = self._root / test_id / "test.json"
            if legacy.exists():
                return self._load_test(legacy)
            raise FileNotFoundError(f"Test {test_id!r} not found at {path}")
        return self._load_test(path)

    def save_test(self, test: RecordedTest, branch: str | None = None) -> Path:
        path = self._test_path(test.id, branch)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(test.model_dump_json(indent=2), encoding="utf-8")
        return path

    def save_test_atomic(self, test: RecordedTest, branch: str | None = None) -> Path:
        """Atomic write: write temp file then rename."""
        path = self._test_path(test.id, branch)
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

    def delete_test(self, test_id: str, branch: str | None = None) -> None:
        test_dir = self._branch_root(branch) / test_id
        if test_dir.exists():
            shutil.rmtree(test_dir)
            return
        # Fallback legacy
        legacy_dir = self._root / test_id
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir)

    # ------------------------------------------------------------------
    # ExecutionReport CRUD
    # ------------------------------------------------------------------

    def list_reports(self, test_id: str, branch: str | None = None) -> list[ExecutionReport]:
        reports_dir = self._branch_root(branch) / test_id / "reports"
        if not reports_dir.exists():
            # Fallback legacy
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

    def get_report(self, test_id: str, report_id: str, branch: str | None = None) -> ExecutionReport:
        path = self._branch_root(branch) / test_id / "reports" / f"{report_id}.json"
        if not path.exists():
            path = self._root / test_id / "reports" / f"{report_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Report {report_id!r} not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ExecutionReport.model_validate(data)

    def save_report(self, report: ExecutionReport, branch: str | None = None) -> Path:
        reports_dir = self._branch_root(branch) / report.test_id / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / f"{report.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        self._prune_reports(report.test_id, branch)
        return path

    def _prune_reports(self, test_id: str, branch: str | None = None) -> None:
        """Prune old report files based on configured retention."""
        if not self._max_reports_per_test or self._max_reports_per_test <= 0:
            return

        reports_dir = self._branch_root(branch) / test_id / "reports"
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

    def update_cnl(self, test_id: str, cnl_text: str, branch: str | None = None) -> RecordedTest:
        """Update CNL text only (legacy behavior - doesn't regenerate actions).
        
        WARNING: This only updates the CNL display text. The underlying actions
        are NOT regenerated, so edits won't affect test execution.
        
        Use update_cnl_with_regeneration() for full CNL editing support.
        """
        from sat.cnl.parser import parse_cnl
        from sat.core.models import CNLStep as CoreCNLStep

        test = self.get_test(test_id, branch)
        test.cnl = cnl_text
        parsed = parse_cnl(cnl_text)
        
        # Convert parsed CNL steps to core CNL steps
        test.cnl_steps = [
            CoreCNLStep(
                step_number=s.step_number,
                raw_cnl=s.raw_cnl,
                action_type=s.action_type,
                element_query=s.element_query,
                value=s.value,
                element_type_hint=s.element_type_hint,
            )
            for s in parsed.steps
        ]

        # Merge CNL steps back into actions
        cnl_by_step = {cs.step_number: cs for cs in parsed.steps}
        for action in test.actions:
            cnl_step = cnl_by_step.get(action.step_number)
            if cnl_step:
                action.cnl_step = cnl_step.raw_cnl

        self.save_test_atomic(test, branch)
        return test

    async def update_cnl_with_regeneration(
        self,
        test_id: str,
        cnl_text: str,
        config: "SATConfig",
        branch: str | None = None,
        preserve_selectors: bool = True,
    ) -> tuple[RecordedTest, "RegenerationReport"]:
        """Update CNL and regenerate actions from the edited CNL.
        
        This is the recommended way to edit CNL as it ensures the underlying
        actions are updated to match the edited CNL text.
        
        Args:
            test_id: ID of the test to update
            cnl_text: New CNL text
            config: SAT configuration (needed for browser automation)
            branch: Optional branch name
            preserve_selectors: If True, preserve selectors for unchanged steps
            
        Returns:
            Tuple of (updated_test, regeneration_report)
        """
        from sat.services.cnl_regenerator import CNLRegenerator, RegenerationReport

        test = self.get_test(test_id, branch)
        
        # Create regenerator
        regenerator = CNLRegenerator(config)
        
        # Regenerate actions
        new_actions, report = await regenerator.regenerate_actions(
            test=test,
            new_cnl=cnl_text,
            preserve_selectors=preserve_selectors,
        )
        
        # Update test with new data
        test.cnl = cnl_text
        test.actions = new_actions
        
        # Re-parse CNL to update cnl_steps
        from sat.cnl.parser import parse_cnl
        from sat.core.models import CNLStep as CoreCNLStep
        
        parsed = parse_cnl(cnl_text)
        test.cnl_steps = [
            CoreCNLStep(
                step_number=s.step_number,
                raw_cnl=s.raw_cnl,
                action_type=s.action_type,
                element_query=s.element_query,
                value=s.value,
                element_type_hint=s.element_type_hint,
            )
            for s in parsed.steps
        ]
        
        # Save atomically
        self.save_test_atomic(test, branch)
        
        return test, report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _test_path(self, test_id: str, branch: str | None = None) -> Path:
        return self._branch_root(branch) / test_id / "test.json"

    @staticmethod
    def _load_test(path: Path) -> RecordedTest:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RecordedTest.model_validate(data)
