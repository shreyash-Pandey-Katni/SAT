"""CNL Runner — executes raw CNL text against a live browser and records
the result as a new :class:`RecordedTest`.

Flow
----
1. Parse CNL text → list of :class:`CNLStep` + conditional blocks.
2. Open browser → navigate to *start_url*.
3. For each CNL step (with conditional evaluation and variable substitution):
   a) Resolve the target element via the **StrategyChain**
      (Selector → Embedding → VLM) — the same pipeline the Executor uses.
   b) Extract a selector snapshot for future fast-path replay.
   c) Perform the action (click / type / select / store / …).
4. Package every captured step into a :class:`RecordedAction` and return
   a complete :class:`RecordedTest` ready for storage.

On the *first* run no selectors exist, so ``SelectorStrategy`` falls through
and ``EmbeddingStrategy`` (or ``VLMStrategy``) finds elements semantically.
The discovered selectors are persisted in the resulting test so that
subsequent re-executions via the **Executor** use fast CSS lookups and
auto-heal if the page changes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from playwright.async_api import ElementHandle, Page

from sat.cnl.models import (
    CNLConditionalBlock,
    CNLCondition,
    CNLStep,
    ConditionType,
)
from sat.cnl.parser import parse_cnl
from sat.cnl.variables import VariableContext, load_variables
from sat.config import SATConfig
from sat.core.models import (
    ActionType,
    CNLStep as CoreCNLStep,
    RecordedAction,
    RecordedTest,
    ResolutionMethod,
    SelectorInfo,
)
from sat.core.playwright_manager import PlaywrightManager
from sat.executor.strategies.embedding_strategy import EmbeddingStrategy
from sat.executor.strategies.selector_strategy import SelectorStrategy
from sat.executor.strategies.vlm_strategy import VLMStrategy
from sat.executor.strategy_chain import StrategyChain

logger = logging.getLogger(__name__)

# JS snippet to extract selector info from a live element — mirrors the
# one in ``AutoHealer`` so the recorded selectors are identical in shape.
_EXTRACT_SELECTOR_JS = """
(el) => {
    function computeSelector(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        const parts = [];
        let node = el;
        while (node && node.tagName !== 'BODY') {
            let sel = node.tagName.toLowerCase();
            if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
            let nth = 1;
            let sib = node.previousSibling;
            while (sib) {
                if (sib.nodeType === 1 && sib.tagName === node.tagName) nth++;
                sib = sib.previousSibling;
            }
            sel += ':nth-of-type(' + nth + ')';
            parts.unshift(sel);
            node = node.parentElement;
        }
        return parts.join(' > ');
    }
    function computeXPath(el) {
        const parts = [];
        let node = el;
        while (node && node.nodeType === 1) {
            let idx = 1, sib = node.previousSibling;
            while (sib) {
                if (sib.nodeType === 1 && sib.nodeName === node.nodeName) idx++;
                sib = sib.previousSibling;
            }
            parts.unshift(node.nodeName.toLowerCase() + '[' + idx + ']');
            node = node.parentElement;
        }
        return '/' + parts.join('/');
    }
    return {
        tag_name: el.tagName.toLowerCase(),
        id: el.id || null,
        class_name: (el.className || '').substring(0, 200) || null,
        name: el.getAttribute('name'),
        text_content: (el.textContent || '').trim().substring(0, 200) || null,
        aria_label: el.getAttribute('aria-label'),
        placeholder: el.getAttribute('placeholder'),
        data_testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
        href: el.getAttribute('href'),
        role: el.getAttribute('role'),
        input_type: el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : null,
        outer_html_snippet: el.outerHTML.substring(0, 500),
        parent_html_snippet: el.parentElement ? el.parentElement.outerHTML.substring(0, 300) : null,
        css: computeSelector(el),
        xpath: computeXPath(el),
        in_shadow_dom: !!el.getRootNode().host,
    };
}
"""

StepCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class CNLRunner:
    """Executes CNL text in a live browser and builds a RecordedTest.

    Element resolution is delegated to the same :class:`StrategyChain` that
    the :class:`Executor` uses (**Selector → Embedding → VLM**).  On the
    first run there are no saved selectors so the chain falls through to
    the semantic strategies; the discovered selectors are written into the
    resulting :class:`RecordedTest` for fast CSS replay on re-execution.
    """

    def __init__(self, config: SATConfig) -> None:
        self._config = config
        self._recordings_dir = Path(config.recorder.output_dir)
        self._step_callbacks: list[StepCallback] = []

        # Build the same strategy chain the Executor uses
        ec = config.executor
        strategies_map = {
            "selector": lambda: SelectorStrategy(timeout_ms=ec.selector.timeout_ms),
            "embedding": lambda: EmbeddingStrategy(config=ec.embedding),
            "vlm": lambda: VLMStrategy(config=ec.vlm),
        }
        self._strategy_chain = StrategyChain([
            strategies_map[name]()
            for name in ec.strategies
            if name in strategies_map
        ])

    def on_step(self, cb: StepCallback) -> None:
        self._step_callbacks.append(cb)

    async def run(
        self,
        cnl_text: str,
        start_url: str,
        name: str = "CNL Test",
        variables: dict[str, str] | None = None,
    ) -> RecordedTest:
        """Parse *cnl_text*, execute it, and return a persisted test."""
        # Build variable context: global → per-test → runtime overrides
        merged_vars = load_variables(
            global_path=self._config.variables.global_file,
            overrides=variables,
        )
        var_ctx = VariableContext(merged_vars)

        parsed = parse_cnl(cnl_text, variables=var_ctx.get_all())
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

        # Build a set of step_numbers belonging to conditional blocks
        # so we know which steps need condition evaluation
        cond_map = self._build_condition_map(parsed.conditional_blocks)

        try:
            for step in parsed.steps:
                # ── Conditional check ────────────────────────────────
                cond_info = cond_map.get(step.step_number)
                if cond_info is not None:
                    cond, is_then_branch = cond_info
                    should_run = await self._evaluate_condition(active_page, cond)
                    if is_then_branch and not should_run:
                        continue  # skip then-branch steps
                    if not is_then_branch and should_run:
                        continue  # skip else-branch steps

                # ── Runtime variable substitution in step values ─────
                step = self._substitute_step(step, var_ctx)

                result = await self._execute_cnl_step(
                    step, active_page, screenshots_dir, dom_dir, test_id,
                    var_ctx,
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
    # Condition helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_condition_map(
        blocks: list[CNLConditionalBlock],
    ) -> dict[int, tuple[CNLCondition, bool]]:
        """Map step_number → (condition, is_then_branch) for conditional steps."""
        result: dict[int, tuple[CNLCondition, bool]] = {}
        for block in blocks:
            for step in block.then_steps:
                result[step.step_number] = (block.condition, True)
            for step in block.else_steps:
                result[step.step_number] = (block.condition, False)
        return result

    async def _evaluate_condition(
        self, page: Page, condition: CNLCondition,
    ) -> bool:
        """Evaluate a CNL condition against the current page state.

        Conditions are transient checks — they use the StrategyChain to find
        the element but do *not* persist selectors.
        """
        # Build a lightweight RecordedAction for the strategy chain
        stub = RecordedAction(
            step_number=0,
            action_type=ActionType.CLICK,  # irrelevant for resolution
            url=page.url,
            tab_id=str(id(page)),
            cnl_step=condition.raw_cnl if hasattr(condition, "raw_cnl") else (
                f'{condition.element_query or ""} {condition.element_type_hint or ""}'.strip()
            ),
            selector=None,  # no saved selector → Embedding will resolve
        )

        element, _method, _score, _trace = (
            await self._strategy_chain.resolve_element_with_trace(page, stub)
        )

        if element is None:
            # Element not found — IS_HIDDEN is True, everything else False
            return condition.condition_type == ConditionType.IS_HIDDEN

        match condition.condition_type:
            case ConditionType.IS_VISIBLE:
                return await element.is_visible()
            case ConditionType.IS_HIDDEN:
                return not await element.is_visible()
            case ConditionType.CONTAINS_TEXT:
                text = await element.text_content() or ""
                return (condition.expected_value or "") in text
            case ConditionType.IS_EQUAL:
                text = (await element.text_content() or "").strip()
                return text == (condition.expected_value or "")
        return False

    @staticmethod
    def _substitute_step(step: CNLStep, var_ctx: VariableContext) -> CNLStep:
        """Return a copy of *step* with ``${var}`` replaced in value fields."""
        new_value = var_ctx.substitute(step.value) if step.value else step.value
        new_query = var_ctx.substitute(step.element_query) if step.element_query else step.element_query
        if new_value != step.value or new_query != step.element_query:
            return step.model_copy(update={"value": new_value, "element_query": new_query})
        return step

    # ------------------------------------------------------------------
    # Assertion execution
    # ------------------------------------------------------------------

    async def _execute_assertion(
        self,
        step: CNLStep,
        page: Page,
        element: ElementHandle,
        base: dict[str, Any],
    ) -> None:
        """Execute an assertion and raise AssertionError if it fails.
        
        Updates base dict with assertion metadata.
        Raises AssertionError with detailed message if assertion fails.
        """
        if not step.assertion_type:
            return

        # Determine what attribute to check (text or value)
        check_attribute = step.store_attribute or "text"
        actual_value = None
        passed = False

        # Evaluate assertion based on type
        match step.assertion_type:
            case ConditionType.IS_VISIBLE:
                passed = await element.is_visible()
                actual_value = "visible" if passed else "hidden"

            case ConditionType.IS_HIDDEN:
                is_visible = await element.is_visible()
                passed = not is_visible
                actual_value = "hidden" if passed else "visible"

            case ConditionType.CONTAINS_TEXT:
                # Check text or value based on store_attribute
                if check_attribute == "value":
                    # Get input value for form fields
                    actual_value = await element.input_value() if hasattr(element, "input_value") else (
                        await page.evaluate("(el) => el.value || ''", element)
                    )
                else:
                    # Get text content
                    actual_value = await element.text_content() or ""

                expected = step.assertion_expected or ""
                passed = expected in actual_value

            case ConditionType.IS_EQUAL:
                # Check text or value based on store_attribute
                if check_attribute == "value":
                    # Get input value for form fields
                    actual_value = await element.input_value() if hasattr(element, "input_value") else (
                        await page.evaluate("(el) => el.value || ''", element)
                    )
                    actual_value = actual_value.strip()
                else:
                    # Get text content
                    actual_value = (await element.text_content() or "").strip()

                expected = (step.assertion_expected or "").strip()
                passed = actual_value == expected

        # Store assertion metadata
        base["metadata"] = {
            "assertion_type": step.assertion_type.value,
            "assertion_expected": step.assertion_expected,
            "assertion_actual": actual_value,
            "assertion_result": "passed" if passed else "failed",
        }

        # Log result
        if passed:
            logger.info(
                "  → assertion passed: %s",
                step.assertion_type.value,
            )
        else:
            # Format detailed error message
            error_msg = self._format_assertion_error(
                step.step_number,
                step.raw_cnl,
                step.assertion_type,
                step.assertion_expected,
                actual_value,
            )
            logger.error("  → assertion failed: %s", step.assertion_type.value)
            raise AssertionError(error_msg)

    @staticmethod
    def _format_assertion_error(
        step_number: int,
        raw_cnl: str,
        assertion_type: ConditionType,
        expected: str | None,
        actual: str | None,
    ) -> str:
        """Format a detailed assertion error message."""
        msg = f"Assertion failed at step {step_number}:\n"
        msg += f"  CNL: {raw_cnl}\n"
        msg += f"  Type: {assertion_type.value}\n"

        if expected is not None:
            msg += f"  Expected: {expected!r}\n"
        if actual is not None:
            msg += f"  Actual: {actual!r}\n"

        return msg

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
        var_ctx: VariableContext | None = None,
    ) -> dict[str, Any]:
        """Execute one CNL step and return a dict ready for RecordedAction."""
        logger.info("[cnl-run step %d] %s", step.step_number, step.raw_cnl)

        base: dict[str, Any] = {
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

        # ── Element actions — delegate to StrategyChain ──────────────────
        element, method, score = await self._resolve_element(page, step)
        if element is None:
            raise RuntimeError(
                f"Step {step.step_number}: could not find element "
                f"for '{step.raw_cnl}' — all strategies exhausted"
            )

        logger.info(
            "  → resolved via %s (score=%s)",
            method.value,
            f"{score:.4f}" if score is not None else "N/A",
        )

        # Extract selector info from the live element for future fast replay
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

        # ── Perform the action ───────────────────────────────────────────
        if step.action_type == ActionType.CLICK:
            await element.click()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass

        elif step.action_type == ActionType.TYPE:
            value = step.value or ""
            element = await self._ensure_fillable(page, element)
            await element.fill(value)
            base["value"] = value

        elif step.action_type == ActionType.SELECT:
            value = step.value or ""
            await element.select_option(value=value)
            base["value"] = value

        elif step.action_type == ActionType.HOVER:
            await element.hover()

        elif step.action_type == ActionType.STORE:
            # Extract value from element and store into variable context
            attr = (step.store_attribute or "text").lower()
            if attr == "text":
                stored_value = (await element.text_content() or "").strip()
            elif attr == "value":
                stored_value = await element.input_value() if hasattr(element, "input_value") else (
                    await page.evaluate("(el) => el.value || ''", element)
                )
            else:
                stored_value = await page.evaluate(
                    f"(el) => el.getAttribute('{attr}') || ''", element
                )
            if var_ctx and step.variable_name:
                var_ctx.set(step.variable_name, stored_value)
                logger.info(
                    "  → stored ${%s} = %r", step.variable_name, stored_value
                )
            base["value"] = stored_value
            base["metadata"] = {
                "variable_name": step.variable_name,
                "store_attribute": attr,
            }

        elif step.action_type == ActionType.ASSERT:
            # Execute assertion and fail test if it doesn't pass
            await self._execute_assertion(step, page, element, base)

        return base

    # ------------------------------------------------------------------
    # Ensure element is fillable (input / textarea / contenteditable)
    # ------------------------------------------------------------------

    @staticmethod
    async def _ensure_fillable(
        page: Page, element: ElementHandle,
    ) -> ElementHandle:
        """Return an element that Playwright's ``fill()`` can target.

        If *element* is already a ``<input>``, ``<textarea>``, ``<select>``
        or ``[contenteditable]`` it is returned as-is.  Otherwise we look
        for a fillable descendant, then an ancestor, then a sibling —
        covering the common case where VLM or Embedding resolve a wrapper
        (``<div>``, ``<label>``, etc.) instead of the actual form control.
        """
        js = """
        (el) => {
            const TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
            const isFillable = (e) =>
                TAGS.has(e.tagName) || e.isContentEditable;

            if (isFillable(el)) return null;            // already good

            // 1. Descendant
            const child = el.querySelector('input, textarea, select, [contenteditable]');
            if (child) return child;

            // 2. <label for="…"> → getElementById
            const lbl = el.closest('label');
            if (lbl) {
                const forId = lbl.getAttribute('for');
                if (forId) {
                    const target = document.getElementById(forId);
                    if (target && isFillable(target)) return target;
                }
                const nested = lbl.querySelector('input, textarea, select, [contenteditable]');
                if (nested) return nested;
            }

            // 3. Walk up max 3 ancestors looking for a fillable child
            let parent = el.parentElement;
            for (let i = 0; i < 3 && parent; i++, parent = parent.parentElement) {
                const found = parent.querySelector('input, textarea, select, [contenteditable]');
                if (found) return found;
            }

            return null;  // give up — caller will use fill() on original
        }
        """
        try:
            handle = await page.evaluate_handle(js, element)
            better = handle.as_element()
            if better:
                logger.debug("_ensure_fillable: redirected to real input element")
                return better
        except Exception as exc:
            logger.debug("_ensure_fillable JS error: %s", exc)

        return element

    # ------------------------------------------------------------------
    # Element resolution via StrategyChain
    # ------------------------------------------------------------------

    # Map CNL element-type hints to HTML tag names / ARIA roles so the
    # embedding query includes the same vocabulary the DOM uses.
    _TYPE_HINT_MAP: dict[str, tuple[str, str | None]] = {
        "textfield": ("input", "textbox"),
        "button":    ("button", "button"),
        "link":      ("a", "link"),
        "checkbox":  ("input", "checkbox"),
        "dropdown":  ("select", None),
        "radio":     ("input", "radio"),
        "tab":       ("button", "tab"),
        "menu":      ("button", "menuitem"),
        "image":     ("img", None),
        "icon":      ("span", None),
        "text":      ("span", None),
    }

    async def _resolve_element(
        self, page: Page, step: CNLStep,
    ) -> tuple[ElementHandle | None, ResolutionMethod, float | None]:
        """Resolve a CNL step's target element through the StrategyChain.

        Builds a stub :class:`RecordedAction` enriched with a
        :class:`SelectorInfo` derived from the parsed CNL fields
        (``element_query``, ``element_type_hint``).  This gives the
        :class:`EmbeddingStrategy` semantic hints (placeholder, text,
        tag name, role) that closely match the DOM descriptions, so it
        can score candidates accurately instead of falling through to
        VLM.
        """
        # Derive selector hints from the CNL parse results
        query = step.element_query or ""
        hint = (step.element_type_hint or "").lower()
        tag, role = self._TYPE_HINT_MAP.get(hint, (None, None))

        # The element_query has the format "Label TypeHint" (e.g.
        # "Enter password TextField").  Strip the type suffix so that
        # the placeholder/text matches the DOM value exactly.
        # Use case-insensitive comparison — _normalise_type() capitalises
        # the hint ("TextField" → "Textfield") while the query preserves
        # the original casing from the CNL source.
        label = query
        if step.element_type_hint and label.lower().endswith(
            step.element_type_hint.lower()
        ):
            label = label[: -len(step.element_type_hint)].rstrip()

        # For text-input types the label is typically a placeholder;
        # for others it is visible text content.
        is_input = hint in ("textfield", "dropdown", "checkbox", "radio")
        selector_hint = SelectorInfo(
            tag_name=tag or "unknown",
            placeholder=label if is_input else None,
            text_content=None if is_input else label,
            role=role,
        )

        stub = RecordedAction(
            step_number=step.step_number,
            action_type=step.action_type,
            url=page.url,
            tab_id=str(id(page)),
            cnl_step=step.element_query or step.raw_cnl,
            selector=selector_hint,
        )
        element, method, score, _trace = (
            await self._strategy_chain.resolve_element_with_trace(page, stub)
        )
        return element, method, score

    # ------------------------------------------------------------------
    # Selector extraction from live element
    # ------------------------------------------------------------------

    async def _extract_selector(
        self, page: Page, element: ElementHandle,
    ) -> SelectorInfo:
        """Extract selector info from a live DOM element.

        The returned :class:`SelectorInfo` is written into the test's
        :class:`RecordedAction` so that future Executor re-runs can use
        the fast :class:`SelectorStrategy` path.
        """
        data: dict = await page.evaluate(_EXTRACT_SELECTOR_JS, element)
        return SelectorInfo(
            tag_name=data.get("tag_name", "unknown"),
            css=data.get("css"),
            xpath=data.get("xpath"),
            id=data.get("id") or None,
            name=data.get("name"),
            class_name=data.get("class_name") or None,
            text_content=data.get("text_content") or None,
            aria_label=data.get("aria_label"),
            placeholder=data.get("placeholder"),
            data_testid=data.get("data_testid"),
            href=data.get("href"),
            role=data.get("role"),
            input_type=data.get("input_type"),
            outer_html_snippet=data.get("outer_html_snippet", ""),
            parent_html_snippet=data.get("parent_html_snippet"),
            in_shadow_dom=data.get("in_shadow_dom", False),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _find_tab(
        self, current: Page, title_or_url: str,
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
