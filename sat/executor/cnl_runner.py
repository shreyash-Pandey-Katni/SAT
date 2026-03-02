"""CNL Runner — executes raw CNL text against a live browser and records
the result as a new :class:`RecordedTest`.

Flow
----
1. Parse CNL text → list of :class:`CNLStep`.
2. Open browser → navigate to *start_url*.
3. For each CNL step:
   a) Resolve the target element using Playwright's semantic locators
      (role, text, placeholder, label).
   b) Capture a selector snapshot + screenshot.
   c) Perform the action (click / type / select / …).
4. Package every captured step into a :class:`RecordedAction` and return
   a complete :class:`RecordedTest` ready for storage.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from playwright.async_api import ElementHandle, Frame, Page

from sat.cnl.models import CNLStep
from sat.cnl.parser import parse_cnl
from sat.config import SATConfig
from sat.core.models import (
    ActionType,
    CNLStep as CoreCNLStep,
    RecordedAction,
    RecordedTest,
    SelectorInfo,
)
from sat.core.playwright_manager import PlaywrightManager

logger = logging.getLogger(__name__)

StepCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class CNLRunner:
    """Executes CNL text in a live browser and builds a RecordedTest."""

    def __init__(self, config: SATConfig) -> None:
        self._config = config
        self._recordings_dir = Path(config.recorder.output_dir)
        self._step_callbacks: list[StepCallback] = []

    def on_step(self, cb: StepCallback) -> None:
        self._step_callbacks.append(cb)

    async def run(
        self,
        cnl_text: str,
        start_url: str,
        name: str = "CNL Test",
    ) -> RecordedTest:
        """Parse *cnl_text*, execute it, and return a persisted test."""
        parsed = parse_cnl(cnl_text)
        if parsed.errors:
            msgs = "; ".join(
                f"L{e.line}: {e.message}" for e in parsed.errors
            )
            raise ValueError(f"CNL parse errors: {msgs}")

        test_id = str(uuid.uuid4())
        test_dir = self._recordings_dir / test_id
        screenshots_dir = test_dir / "screenshots"
        dom_dir = test_dir / "dom_snapshots"
        for d in (test_dir, screenshots_dir, dom_dir):
            d.mkdir(parents=True, exist_ok=True)

        manager = PlaywrightManager(self._config)
        page = await manager.start(url=start_url)
        active_page: Page = page

        actions: list[RecordedAction] = []

        try:
            for step in parsed.steps:
                result = await self._execute_cnl_step(
                    step, active_page, screenshots_dir, dom_dir, test_id,
                )
                new_page = result.pop("_new_page", None)
                if new_page is not None:
                    active_page = new_page

                action = RecordedAction(**result)
                actions.append(action)

                # Notify listeners
                for cb in self._step_callbacks:
                    try:
                        await cb({
                            "step_number": action.step_number,
                            "action_type": action.action_type.value,
                            "cnl_step": action.cnl_step,
                            "status": "passed",
                        })
                    except Exception:
                        pass
        except Exception as exc:
            # Report the failure to listeners then re-raise
            for cb in self._step_callbacks:
                try:
                    await cb({
                        "step_number": len(actions) + 1,
                        "action_type": "error",
                        "cnl_step": "",
                        "status": "failed",
                        "error": str(exc),
                    })
                except Exception:
                    pass
            raise
        finally:
            await manager.stop()

        test = RecordedTest(
            id=test_id,
            name=name,
            created_at=datetime.utcnow(),
            start_url=start_url,
            browser=self._config.browser.type,
            actions=actions,
            cnl=cnl_text,
            cnl_steps=[
                CoreCNLStep(
                    step_number=s.step_number,
                    raw_cnl=s.raw_cnl,
                    action_type=s.action_type,
                    element_query=s.element_query,
                    value=s.value,
                    element_type_hint=s.element_type_hint,
                )
                for s in parsed.steps
            ],
        )
        return test

    # ------------------------------------------------------------------
    # Per-step execution
    # ------------------------------------------------------------------

    async def _execute_cnl_step(
        self,
        step: CNLStep,
        page: Page,
        screenshots_dir: Path,
        dom_dir: Path,
        test_id: str,
    ) -> dict[str, Any]:
        """Execute one CNL step and return a dict ready for RecordedAction."""
        logger.info("[cnl-run step %d] %s", step.step_number, step.raw_cnl)

        base = {
            "step_number": step.step_number,
            "timestamp": datetime.utcnow().isoformat(),
            "action_type": step.action_type.value,
            "url": page.url,
            "tab_id": str(id(page)),
            "cnl_step": step.raw_cnl,
            "viewport": await self._viewport(page),
        }

        # ── Page-level actions (no element resolution) ───────────────────
        if step.action_type == ActionType.NAVIGATE:
            url = step.value or ""
            await page.goto(url, wait_until="domcontentloaded")
            base["url"] = url
            base["value"] = url
            return base

        if step.action_type == ActionType.NEW_TAB:
            url = step.value or ""
            new_page = await page.context.new_page()
            if url and url != "about:blank":
                await new_page.goto(url, wait_until="domcontentloaded")
            base["value"] = url
            base["url"] = new_page.url
            base["tab_id"] = str(id(new_page))
            base["_new_page"] = new_page
            return base

        if step.action_type == ActionType.SWITCH_TAB:
            title_or_url = step.value or ""
            target = await self._find_tab(page, title_or_url)
            if target:
                await target.bring_to_front()
                base["url"] = target.url
                base["tab_id"] = str(id(target))
                base["value"] = title_or_url
                base["metadata"] = {"title": await target.title()}
                base["_new_page"] = target
            return base

        if step.action_type == ActionType.CLOSE_TAB:
            remaining = [
                p for p in page.context.pages
                if p != page and not p.is_closed()
            ]
            await page.close()
            if remaining:
                await remaining[-1].bring_to_front()
                base["_new_page"] = remaining[-1]
            return base

        if step.action_type == ActionType.SCROLL:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            return base

        # ── Element actions ──────────────────────────────────────────────
        element, locator_info = await self._resolve_element(page, step)
        if element is None:
            raise RuntimeError(
                f"Step {step.step_number}: could not find element "
                f"for '{step.raw_cnl}'"
            )

        # Capture selector info from the live element
        selector = await self._extract_selector(page, element)
        base["selector"] = selector.model_dump()

        # Capture element position
        bbox = await element.bounding_box()
        if bbox:
            base["element_position"] = {
                "x": bbox["x"],
                "y": bbox["y"],
                "width": bbox["width"],
                "height": bbox["height"],
            }

        # Screenshot before action
        scr_path = screenshots_dir / f"step_{step.step_number:04d}.png"
        try:
            await page.screenshot(path=str(scr_path), type="png")
            base["screenshot_path"] = (
                f"recordings/{test_id}/screenshots/step_{step.step_number:04d}.png"
            )
        except Exception:
            pass

        # Perform the action
        if step.action_type == ActionType.CLICK:
            await element.click()
            # Wait for potential navigation / popup
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass

        elif step.action_type == ActionType.TYPE:
            value = step.value or ""
            await element.fill(value)
            base["value"] = value

        elif step.action_type == ActionType.SELECT:
            value = step.value or ""
            await element.select_option(value=value)
            base["value"] = value

        elif step.action_type == ActionType.HOVER:
            await element.hover()

        return base

    # ------------------------------------------------------------------
    # Element resolution via Playwright semantic locators
    # ------------------------------------------------------------------

    async def _resolve_element(
        self, page: Page, step: CNLStep
    ) -> tuple[ElementHandle | None, str]:
        """Use Playwright's smart locators to find the element described by
        the CNL step's *element_query* and *element_type_hint*.

        Returns ``(handle, description)`` or ``(None, "")`` on failure.
        """
        query = step.element_query or ""
        hint = (step.element_type_hint or "").lower()
        # Strip the element type from the query if it was appended
        label = query
        if hint and query.lower().endswith(hint):
            label = query[: -len(hint)].strip()

        locators = self._build_cnl_locators(page, label, hint, step)

        for desc, loc in locators:
            try:
                count = await loc.count()
                if count >= 1:
                    handle = await loc.first.element_handle()
                    if handle:
                        # Verify it's visible
                        if await handle.is_visible():
                            logger.info(
                                "  → resolved via %s (count=%d)", desc, count
                            )
                            return handle, desc
            except Exception as exc:
                logger.debug("  locator %s failed: %s", desc, exc)
                continue

        logger.warning(
            "Step %d: no element found for query=%r hint=%r",
            step.step_number, query, hint,
        )
        return None, ""

    def _build_cnl_locators(self, page: Page, label: str, hint: str, step: CNLStep):
        """Yield ``(description, Locator)`` pairs in priority order."""
        locators: list[tuple[str, Any]] = []

        # Map CNL element type hints to Playwright ARIA roles
        role_map = {
            "button": "button",
            "link": "link",
            "textfield": "textbox",
            "checkbox": "checkbox",
            "dropdown": "combobox",
            "radio": "radio",
            "tab": "tab",
            "menu": "menu",
            "image": "img",
        }

        role = role_map.get(hint)

        # 1. Role + name (most semantic)
        if role and label:
            locators.append((
                f'get_by_role("{role}", name="{label}")',
                page.get_by_role(role, name=label),  # type: ignore[arg-type]
            ))

        # 2. Placeholder (for text fields)
        if hint in ("textfield", "") and label:
            locators.append((
                f'get_by_placeholder("{label}")',
                page.get_by_placeholder(label),
            ))

        # 3. Label
        if label:
            locators.append((
                f'get_by_label("{label}")',
                page.get_by_label(label),
            ))

        # 4. Exact text
        if label:
            locators.append((
                f'get_by_text("{label}", exact)',
                page.get_by_text(label, exact=True),
            ))

        # 5. Partial text
        if label:
            locators.append((
                f'get_by_text("{label}")',
                page.get_by_text(label),
            ))

        # 6. Title attribute
        if label:
            locators.append((
                f'get_by_title("{label}")',
                page.get_by_title(label),
            ))

        # 7. CSS with text content (tag + text)
        tag = self._hint_to_tag(hint)
        if tag and label:
            locators.append((
                f'{tag}:has-text("{label}")',
                page.locator(f'{tag}:has-text("{label}")'),
            ))

        # 8. Generic text fallback (any element)
        if label:
            locators.append((
                f'*:has-text("{label}") (generic)',
                page.locator(f'*:has-text("{label}")').last,
            ))

        return locators

    @staticmethod
    def _hint_to_tag(hint: str) -> str | None:
        return {
            "button": "button",
            "link": "a",
            "textfield": "input",
            "checkbox": "input[type=checkbox]",
            "radio": "input[type=radio]",
            "dropdown": "select",
            "image": "img",
        }.get(hint)

    # ------------------------------------------------------------------
    # Selector extraction from live element
    # ------------------------------------------------------------------

    async def _extract_selector(
        self, page: Page, element: ElementHandle
    ) -> SelectorInfo:
        """Build a :class:`SelectorInfo` by evaluating properties on the live element."""
        info: dict = await page.evaluate("""(el) => {
            function cssPath(e) {
                const parts = [];
                while (e && e.nodeType === 1) {
                    let sel = e.localName;
                    if (e.id) { parts.unshift('#' + e.id); break; }
                    let sib = e, nth = 1;
                    while (sib = sib.previousElementSibling) { if (sib.localName === sel) nth++; }
                    if (nth > 1) sel += ':nth-of-type(' + nth + ')';
                    parts.unshift(sel);
                    e = e.parentElement;
                }
                return parts.join(' > ');
            }
            const outer = el.outerHTML || '';
            return {
                tag_name: el.tagName.toLowerCase(),
                css: cssPath(el),
                id: el.id || null,
                name: el.getAttribute('name') || null,
                class_name: el.className || null,
                text_content: (el.textContent || '').trim().slice(0, 200) || null,
                aria_label: el.getAttribute('aria-label') || null,
                placeholder: el.getAttribute('placeholder') || null,
                data_testid: el.getAttribute('data-testid') || null,
                href: el.getAttribute('href') || null,
                role: el.getAttribute('role') || null,
                input_type: el.getAttribute('type') || null,
                outer_html_snippet: outer.slice(0, 300),
                parent_html_snippet: (el.parentElement?.innerHTML || '').slice(0, 400) || null,
                in_shadow_dom: !!el.getRootNode().host,
            };
        }""", element)
        return SelectorInfo(**info)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _find_tab(
        self, current: Page, title_or_url: str
    ) -> Page | None:
        for p in current.context.pages:
            if p == current or p.is_closed():
                continue
            if title_or_url in (p.url or ""):
                return p
            try:
                t = await p.title()
                if title_or_url in t:
                    return p
            except Exception:
                pass
        return None

    @staticmethod
    async def _viewport(page: Page) -> dict:
        try:
            vp = page.viewport_size or {}
            scroll = await page.evaluate(
                "() => ({scrollX: window.scrollX, scrollY: window.scrollY})"
            )
            return {**vp, **scroll}
        except Exception:
            return {}
