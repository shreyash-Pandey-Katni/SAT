"""Abstract base class for element resolution strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import ElementHandle, Page

from sat.core.models import RecordedAction, ResolutionMethod


class ResolutionStrategy(ABC):
    """Attempts to locate a DOM element for a given :class:`RecordedAction`."""

    @property
    @abstractmethod
    def method(self) -> ResolutionMethod:
        ...

    @abstractmethod
    async def resolve(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, float | None]:
        """Return (element_handle, score) or (None, None) if not found.

        *score* is a confidence value in [0, 1] — may be None for selector strategy.
        """
        ...
