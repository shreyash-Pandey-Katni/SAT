"""NavigationCausationTracker — distinguishes user-initiated navigations from
those caused by click/type interactions.

Only user-initiated navigations (URL-bar changes, back/forward) are recorded
as ActionType.NAVIGATE steps.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse


class NavigationCausationTracker:
    """Tracks recently recorded interactions to detect causation windows."""

    def __init__(self, causation_window_ms: int = 2000) -> None:
        self._window_ms = causation_window_ms
        self._last_interaction_ts: float = 0.0
        # Store expected destination URLs from <a> clicks so we can identify
        # click-caused navigations regardless of timing.
        self._pending_hrefs: set[str] = set()

    # ------------------------------------------------------------------
    # Called by the recorder on every recorded click/type
    # ------------------------------------------------------------------

    def on_user_interaction(
        self,
        action_type: str,
        target_href: str | None = None,
    ) -> None:
        """Register that an interaction just occurred.

        Args:
            action_type:  e.g. "click" | "type" | "select"
            target_href:  href attribute of the clicked element (if any).
        """
        self._last_interaction_ts = time.monotonic()
        if target_href:
            self._pending_hrefs.add(target_href)

    # ------------------------------------------------------------------
    # Called by the framenavigated event handler
    # ------------------------------------------------------------------

    def is_user_initiated(self, new_url: str) -> bool:
        """Return True if the navigation should be recorded as user-initiated.

        A navigation is considered CAUSED by a recent interaction when:
          - It happened within *causation_window_ms* of the last click/type, OR
          - The new URL matches a pending href from a recent <a> click.

        Otherwise it is treated as user-initiated (URL bar change, back/fwd).
        """
        # Check pending hrefs — handles both absolute and relative hrefs.
        new_path = _url_path(new_url)
        for pending in list(self._pending_hrefs):
            # Absolute href: compare full path
            if pending.startswith("http"):
                if _url_path(pending) == new_path:
                    self._pending_hrefs.discard(pending)
                    return False
            else:
                # Relative href ("/dashboard", "page.html", etc.):
                # check if the navigation URL's path ends with the href path
                pending_clean = pending.split("#")[0].split("?")[0]
                if new_path == pending_clean or new_path.endswith(pending_clean):
                    self._pending_hrefs.discard(pending)
                    return False

        # Check time window
        elapsed_ms = (time.monotonic() - self._last_interaction_ts) * 1000
        if elapsed_ms < self._window_ms:
            return False

        return True

    def clear(self) -> None:
        self._last_interaction_ts = 0.0
        self._pending_hrefs.clear()


def _url_path(url: str) -> str:
    """Extract the path portion of a URL, stripping query string and fragment."""
    parsed = urlparse(url)
    return parsed.path or "/"
