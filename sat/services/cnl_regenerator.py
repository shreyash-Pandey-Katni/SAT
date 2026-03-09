"""CNL Regenerator — intelligently regenerates RecordedActions from edited CNL.

This service handles the complex task of updating a RecordedTest when its CNL
is edited, preserving existing selectors where possible while regenerating
actions for changed steps.

Strategy:
1. Parse new CNL and compare with existing CNL steps
2. Match unchanged steps to preserve their selectors
3. Regenerate only changed/new steps via CNLRunner
4. Merge results with preserved selectors
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Coroutine

from sat.cnl.models import CNLStep as ParsedCNLStep
from sat.cnl.parser import parse_cnl
from sat.config import SATConfig
from sat.core.models import ActionType, CNLStep, RecordedAction, RecordedTest

logger = logging.getLogger(__name__)

StepCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


@dataclass
class StepChange:
    """Represents a change between old and new CNL steps."""
    step_number: int
    change_type: str  # "added", "removed", "modified", "preserved"
    old_step: CNLStep | None = None
    new_step: ParsedCNLStep | None = None
    similarity: float = 0.0


@dataclass
class RegenerationReport:
    """Summary of what changed during CNL regeneration."""
    total_steps: int
    added: int
    removed: int
    modified: int
    preserved: int
    changes: list[StepChange]
    warnings: list[str]
    errors: list[str]


class CNLRegenerator:
    """Regenerates RecordedActions from edited CNL with smart selector preservation."""

    def __init__(self, config: SATConfig) -> None:
        self._config = config
        self._step_callbacks: list[StepCallback] = []

    def on_step_progress(self, callback: StepCallback) -> None:
        """Register a callback for step-by-step progress updates."""
        self._step_callbacks.append(callback)

    async def regenerate_actions(
        self,
        test: RecordedTest,
        new_cnl: str,
        preserve_selectors: bool = True,
    ) -> tuple[list[RecordedAction], RegenerationReport]:
        """Regenerate actions from new CNL, optionally preserving selectors.

        Args:
            test: The existing RecordedTest with current actions
            new_cnl: The edited CNL text
            preserve_selectors: If True, preserve selectors for unchanged steps

        Returns:
            Tuple of (new_actions, regeneration_report)
        """
        logger.info("Starting CNL regeneration for test %s", test.id)

        # Parse new CNL
        try:
            parsed = parse_cnl(new_cnl)
        except Exception as exc:
            logger.error("Failed to parse new CNL: %s", exc)
            return test.actions, RegenerationReport(
                total_steps=len(test.cnl_steps),
                added=0,
                removed=0,
                modified=0,
                preserved=0,
                changes=[],
                warnings=[],
                errors=[f"CNL parsing failed: {exc}"],
            )

        # Match old and new steps
        step_mapping = self._match_steps(test.cnl_steps, parsed.steps)
        changes = self._analyze_changes(test.cnl_steps, parsed.steps, step_mapping)

        # Build report
        report = self._build_report(changes)
        logger.info(
            "CNL changes detected: +%d -%d ~%d =%d",
            report.added, report.removed, report.modified, report.preserved,
        )

        # If no changes and preserve_selectors, return existing actions
        if preserve_selectors and report.added == 0 and report.removed == 0 and report.modified == 0:
            logger.info("No changes detected, returning existing actions")
            return test.actions, report

        # Regenerate actions
        # For now, we'll use a simplified approach: if there are changes,
        # we need to re-run CNLRunner to get fresh actions
        # In a full implementation, this would selectively regenerate only changed steps
        
        # Import here to avoid circular dependency
        from sat.executor.cnl_runner import CNLRunner

        runner = CNLRunner(self._config)
        
        # Forward progress callbacks
        for cb in self._step_callbacks:
            runner.on_step(cb)

        try:
            # Run CNL to generate new test
            regenerated_test = await runner.run(
                cnl_text=new_cnl,
                start_url=test.start_url,
                name=test.name,
            )

            # If preserve_selectors, merge with old actions
            if preserve_selectors:
                new_actions = self._merge_actions(
                    test.actions,
                    regenerated_test.actions,
                    step_mapping,
                )
            else:
                new_actions = regenerated_test.actions

            logger.info("CNL regeneration completed successfully")
            return new_actions, report

        except Exception as exc:
            logger.error("CNL regeneration failed: %s", exc)
            report.errors.append(f"Regeneration failed: {exc}")
            return test.actions, report

    def _match_steps(
        self,
        old_steps: list[CNLStep],
        new_steps: list[ParsedCNLStep],
    ) -> dict[int, int | None]:
        """Match new steps to old steps for selector preservation.

        Returns:
            Mapping of new_step_number -> old_step_number (or None if new)
        """
        mapping: dict[int, int | None] = {}

        # Build lookup for old steps
        old_by_cnl: dict[str, CNLStep] = {
            step.raw_cnl.strip(): step for step in old_steps
        }

        for new_step in new_steps:
            new_cnl = new_step.raw_cnl.strip()

            # Try exact match first
            if new_cnl in old_by_cnl:
                old_step = old_by_cnl[new_cnl]
                mapping[new_step.step_number] = old_step.step_number
                logger.debug(
                    "Exact match: new step %d -> old step %d",
                    new_step.step_number, old_step.step_number,
                )
                continue

            # Try fuzzy match based on action type and element query
            best_match: CNLStep | None = None
            best_score = 0.0

            for old_step in old_steps:
                # Must have same action type
                if old_step.action_type != new_step.action_type:
                    continue

                # Calculate similarity of element queries
                similarity = SequenceMatcher(
                    None,
                    old_step.element_query.lower(),
                    new_step.element_query.lower(),
                ).ratio()

                if similarity > best_score and similarity > 0.7:  # 70% threshold
                    best_score = similarity
                    best_match = old_step

            if best_match:
                mapping[new_step.step_number] = best_match.step_number
                logger.debug(
                    "Fuzzy match: new step %d -> old step %d (%.2f similarity)",
                    new_step.step_number, best_match.step_number, best_score,
                )
            else:
                mapping[new_step.step_number] = None
                logger.debug("No match: new step %d is new", new_step.step_number)

        return mapping

    def _analyze_changes(
        self,
        old_steps: list[CNLStep],
        new_steps: list[ParsedCNLStep],
        mapping: dict[int, int | None],
    ) -> list[StepChange]:
        """Analyze what changed between old and new CNL."""
        changes: list[StepChange] = []

        # Track which old steps were matched
        matched_old_steps = set(mapping.values()) - {None}

        # Analyze new steps
        for new_step in new_steps:
            old_step_num = mapping.get(new_step.step_number)

            if old_step_num is None:
                # New step
                changes.append(StepChange(
                    step_number=new_step.step_number,
                    change_type="added",
                    new_step=new_step,
                ))
            else:
                # Find old step
                old_step = next(
                    (s for s in old_steps if s.step_number == old_step_num),
                    None,
                )
                if old_step:
                    # Check if modified
                    if old_step.raw_cnl.strip() == new_step.raw_cnl.strip():
                        changes.append(StepChange(
                            step_number=new_step.step_number,
                            change_type="preserved",
                            old_step=old_step,
                            new_step=new_step,
                            similarity=1.0,
                        ))
                    else:
                        similarity = SequenceMatcher(
                            None,
                            old_step.raw_cnl,
                            new_step.raw_cnl,
                        ).ratio()
                        changes.append(StepChange(
                            step_number=new_step.step_number,
                            change_type="modified",
                            old_step=old_step,
                            new_step=new_step,
                            similarity=similarity,
                        ))

        # Find removed steps
        for old_step in old_steps:
            if old_step.step_number not in matched_old_steps:
                changes.append(StepChange(
                    step_number=old_step.step_number,
                    change_type="removed",
                    old_step=old_step,
                ))

        return changes

    def _build_report(self, changes: list[StepChange]) -> RegenerationReport:
        """Build a summary report from changes."""
        added = sum(1 for c in changes if c.change_type == "added")
        removed = sum(1 for c in changes if c.change_type == "removed")
        modified = sum(1 for c in changes if c.change_type == "modified")
        preserved = sum(1 for c in changes if c.change_type == "preserved")

        warnings: list[str] = []
        if removed > 0:
            warnings.append(f"{removed} step(s) will be removed")
        if modified > 0:
            warnings.append(f"{modified} step(s) will be regenerated")

        return RegenerationReport(
            total_steps=len([c for c in changes if c.change_type != "removed"]),
            added=added,
            removed=removed,
            modified=modified,
            preserved=preserved,
            changes=changes,
            warnings=warnings,
            errors=[],
        )

    def _merge_actions(
        self,
        old_actions: list[RecordedAction],
        new_actions: list[RecordedAction],
        step_mapping: dict[int, int | None],
    ) -> list[RecordedAction]:
        """Merge new actions with preserved selectors from old actions."""
        # Import selector merger
        from sat.services.selector_merger import merge_selector

        merged: list[RecordedAction] = []

        for new_action in new_actions:
            old_step_num = step_mapping.get(new_action.step_number)

            if old_step_num is not None:
                # Find corresponding old action
                old_action = next(
                    (a for a in old_actions if a.step_number == old_step_num),
                    None,
                )

                if old_action and old_action.selector:
                    # Merge selectors
                    merged_action = merge_selector(old_action, new_action)
                    merged.append(merged_action)
                    logger.debug(
                        "Preserved selector for step %d from old step %d",
                        new_action.step_number, old_step_num,
                    )
                else:
                    merged.append(new_action)
            else:
                # New step, use as-is
                merged.append(new_action)

        return merged

# Made with Bob
