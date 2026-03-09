"""Selector Merger — intelligently merges selectors from old and new actions.

When CNL is edited and actions are regenerated, we want to preserve
auto-healed selectors and other valuable selector data from the original
actions where possible.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sat.core.models import RecordedAction, SelectorInfo

logger = logging.getLogger(__name__)


def merge_selector(
    old_action: RecordedAction,
    new_action: RecordedAction,
) -> RecordedAction:
    """Merge selector data from old_action into new_action.

    Priority:
    1. Preserve auto-healed selectors (they're proven to work)
    2. Preserve selectors with heal history (valuable data)
    3. Use new selectors if old ones seem stale
    4. Always preserve heal_history for audit trail

    Args:
        old_action: The original action with potentially healed selectors
        new_action: The newly generated action with fresh selectors

    Returns:
        A copy of new_action with merged selector data
    """
    # Start with new action as base
    merged = new_action.model_copy(deep=True)

    # If old action has no selector, nothing to merge
    if not old_action.selector:
        logger.debug("Old action has no selector, using new selector")
        return merged

    # If old action was auto-healed, strongly prefer its selector
    if old_action.heal_history:
        logger.info(
            "Preserving auto-healed selector for step %d (healed %d times)",
            old_action.step_number,
            len(old_action.heal_history),
        )
        merged.selector = old_action.selector.model_copy(deep=True)
        merged.heal_history = old_action.heal_history.copy()
        merged.last_healed = old_action.last_healed
        return merged

    # If new action has no selector, use old one
    if not new_action.selector:
        logger.debug("New action has no selector, preserving old selector")
        merged.selector = old_action.selector.model_copy(deep=True)
        return merged

    # Both have selectors - decide which to use
    # Prefer new selector if it has more specific attributes
    old_specificity = _calculate_specificity(old_action.selector)
    new_specificity = _calculate_specificity(new_action.selector)

    if new_specificity > old_specificity:
        logger.debug(
            "Using new selector (specificity: %.2f vs %.2f)",
            new_specificity, old_specificity,
        )
        # Keep new selector but preserve heal history
        merged.heal_history = old_action.heal_history.copy()
    else:
        logger.debug(
            "Preserving old selector (specificity: %.2f vs %.2f)",
            old_specificity, new_specificity,
        )
        merged.selector = old_action.selector.model_copy(deep=True)
        merged.heal_history = old_action.heal_history.copy()
        merged.last_healed = old_action.last_healed

    return merged


def _calculate_specificity(selector: SelectorInfo) -> float:
    """Calculate a specificity score for a selector.

    Higher scores indicate more specific/reliable selectors.
    """
    score = 0.0

    # ID is most specific
    if selector.id:
        score += 10.0

    # data-testid is very specific
    if selector.data_testid:
        score += 8.0

    # name attribute is fairly specific
    if selector.name:
        score += 5.0

    # aria-label is good for accessibility
    if selector.aria_label:
        score += 4.0

    # role is useful
    if selector.role:
        score += 3.0

    # CSS selector exists
    if selector.css:
        score += 2.0
        # Shorter CSS selectors are often more stable
        if len(selector.css) < 50:
            score += 1.0

    # XPath exists
    if selector.xpath:
        score += 1.0

    # Text content can help
    if selector.text_content:
        score += 0.5

    return score


def should_preserve_selector(
    old_action: RecordedAction,
    new_action: RecordedAction,
) -> bool:
    """Determine if old selector should be preserved over new one.

    Returns:
        True if old selector should be preserved, False otherwise
    """
    # Always preserve auto-healed selectors
    if old_action.heal_history:
        return True

    # If old action has no selector, can't preserve
    if not old_action.selector:
        return False

    # If new action has no selector, preserve old
    if not new_action.selector:
        return True

    # Compare specificity
    old_spec = _calculate_specificity(old_action.selector)
    new_spec = _calculate_specificity(new_action.selector)

    # Preserve if old is significantly more specific
    return old_spec > new_spec * 1.2  # 20% threshold

# Made with Bob
