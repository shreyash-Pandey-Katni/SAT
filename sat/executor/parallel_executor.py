"""ParallelExecutor — runs multiple RecordedTests concurrently.

Uses ``asyncio.Semaphore`` to limit the number of parallel browser
instances.  Each test is executed by a fresh :class:`Executor` to
ensure complete isolation (separate browser contexts).
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, Callable, Coroutine

from sat.config import SATConfig
from sat.core.models import ExecutionReport, ExecutionStepResult, RecordedTest
from sat.executor.executor import Executor

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, ExecutionStepResult | dict[str, Any]], Coroutine[Any, Any, None]]


class ParallelExecutor:
    """Execute a batch of tests in parallel with bounded concurrency."""

    def __init__(
        self,
        config: SATConfig,
        max_workers: int = 4,
    ) -> None:
        self._config = config
        self._max_workers = max(1, max_workers)
        self._progress_callbacks: list[ProgressCallback] = []

    def on_progress(self, cb: ProgressCallback) -> None:
        """Register a callback invoked with (test_id, step_result) data."""
        self._progress_callbacks.append(cb)

    async def execute_all(
        self,
        tests: list[RecordedTest],
    ) -> list[ExecutionReport]:
        """Run *tests* concurrently and return all reports (same order)."""
        sem = asyncio.Semaphore(self._max_workers)
        results: list[ExecutionReport | Exception] = [None] * len(tests)  # type: ignore[list-item]

        async def _run_one(idx: int, test: RecordedTest) -> None:
            async with sem:
                try:
                    # Each execution gets an independent config copy
                    cfg = copy.deepcopy(self._config)
                    executor = Executor(cfg)

                    # Wire up per-step progress
                    async def _on_step(result: ExecutionStepResult):
                        for cb in self._progress_callbacks:
                            try:
                                await cb(test.id, result)
                            except Exception:
                                pass

                    executor.on_step_complete(_on_step)
                    report = await executor.execute(test)
                    results[idx] = report

                    # Notify completion
                    for cb in self._progress_callbacks:
                        try:
                            await cb(test.id, {
                                "type": "test_done",
                                "test_id": test.id,
                                "test_name": test.name,
                                "status": report.status.value,
                                "passed": report.passed,
                                "failed": report.failed,
                                "duration_s": report.duration_s,
                            })
                        except Exception:
                            pass

                except Exception as exc:
                    logger.error("Parallel execution failed for %s: %s", test.id, exc)
                    results[idx] = exc
                    for cb in self._progress_callbacks:
                        try:
                            await cb(test.id, {
                                "type": "test_error",
                                "test_id": test.id,
                                "test_name": test.name,
                                "error": str(exc),
                            })
                        except Exception:
                            pass

        tasks = [
            asyncio.create_task(_run_one(i, t))
            for i, t in enumerate(tests)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out errored entries — return only successful reports
        reports: list[ExecutionReport] = []
        for r in results:
            if isinstance(r, ExecutionReport):
                reports.append(r)
        return reports
