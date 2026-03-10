"""NavigationCausationTracker — distinguishes user-initiated navigations from
those caused by click/type interactions.

Only user-initiated navigations (URL-bar changes, back/forward) are recorded
as ActionType.NAVIGATE steps.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

# Schemes that never cause a cross-document navigation.
_NON_NAVIGATING_SCHEMES = ("javascript:", "mailto:", "tel:", "blob:", "data:")

# How long (seconds) to suppress navigations after a click on an element
# with a real navigating href.  This covers multi-hop server-side redirect
# chains (302 → 302 → … → final URL) where the final destination URL has a
# completely different path/domain than the original href.
_REDIRECT_CHAIN_WINDOW_S = 5.0


class NavigationCausationTracker:
    """Tracks recently recorded interactions to detect causation windows."""

    def __init__(self, causation_window_ms: int = 2000) -> None:
        self._window_ms = causation_window_ms
        self._last_interaction_ts: float = 0.0
        # Store expected destination URLs from <a> clicks so we can identify
        # click-caused navigations regardless of timing.
        self._pending_hrefs: set[str] = set()
        # Monotonic deadline until which ALL navigations are suppressed.
        # Set when a click has a real navigating href; each suppressed hop
        # renews for ``_window_ms`` to cover subsequent hops in the chain.
        self._suppress_until: float = 0.0

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
            if _is_navigating_href(target_href):
                # Expect a navigation — set a generous window that covers
                # multi-hop redirect chains.
                self._suppress_until = (
                    time.monotonic() + _REDIRECT_CHAIN_WINDOW_S
                )

    # ------------------------------------------------------------------
    # Called by the framenavigated event handler
    # ------------------------------------------------------------------

    def is_user_initiated(self, new_url: str) -> bool:
        """Return True if the navigation should be recorded as user-initiated.

        Three-layer suppression (checked in order):

        1. **Redirect-chain window** — after a click on an element with a real
           navigating ``href``, suppress *all* navigations for up to
           ``_REDIRECT_CHAIN_WINDOW_S`` seconds.  Each suppressed hop renews
           the deadline by ``_window_ms`` so the chain can continue.
        2. **Href path matching** — for navigations whose path matches a
           pending href (handles direct, non-redirect navigations).
        3. **Generic time window** — any navigation within
           ``causation_window_ms`` of the last click/type is suppressed
           (catches clicks on buttons that trigger ``window.location``).
        """
        now = time.monotonic()

        # --- Layer 1: redirect-chain extended window ---
        if self._suppress_until > 0:
            if now < self._suppress_until:
                # Suppress, then renew the deadline for the next hop.
                self._suppress_until = now + (self._window_ms / 1000)
                self._pending_hrefs.clear()
                return False
            # Window expired — clear.
            self._suppress_until = 0.0

        # --- Layer 2: href path matching ---
        if self._matches_pending_href(new_url):
            return False

        # --- Layer 3: generic time window ---
        elapsed_ms = (now - self._last_interaction_ts) * 1000
        if elapsed_ms < self._window_ms:
            return False

        return True

    def clear(self) -> None:
        self._last_interaction_ts = 0.0
        self._pending_hrefs.clear()
        self._suppress_until = 0.0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _matches_pending_href(self, new_url: str) -> bool:
        """Return True (and consume the href) if *new_url* matches a pending href."""
        new_path = _url_path(new_url)
        for pending in list(self._pending_hrefs):
            if pending.startswith("http"):
                if _url_path(pending) == new_path:
                    self._pending_hrefs.discard(pending)
                    return True
            else:
                pending_clean = pending.split("#")[0].split("?")[0]
                if new_path == pending_clean or new_path.endswith(pending_clean):
                    self._pending_hrefs.discard(pending)
                    return True
        return False


def _is_navigating_href(href: str) -> bool:
    """Return True if *href* would cause a real cross-document navigation."""
    if not href:
        return False
    h = href.strip().lower()
    for scheme in _NON_NAVIGATING_SCHEMES:
        if h.startswith(scheme):
            return False
    # Fragment-only hrefs scroll within the page, they don't navigate.
    if h.startswith("#"):
        return False
    return True


def _url_path(url: str) -> str:
    """Extract the path portion of a URL, stripping query string and fragment."""
    parsed = urlparse(url)
    return parsed.path or "/"
