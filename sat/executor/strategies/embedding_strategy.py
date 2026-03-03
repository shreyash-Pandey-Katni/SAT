"""Strategy 2 — Semantic embeddings via Ollama.

Resolution is a two-stage pipeline:

1. **Text-match pass** (fast, no API call) — scores every DOM candidate by
   exact / partial string overlap of the element's visible text, placeholder,
   or aria-label against the CNL label.  Short, exact labels like
   "CloudStreams" or "Log in" resolve here with score 1.0.

2. **Embedding pass** — falls back to Ollama semantic embeddings when the
   text-match pass yields no confident hit (score < TEXT_MATCH_THRESHOLD).
   Form-control elements (``<input placeholder="…">``) have no visible text so
   only this path can identify them; the structured query format produces
   cosine scores >= 0.88 for well-labelled inputs.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from playwright.async_api import ElementHandle, Frame, Page

from sat.config import EmbeddingStrategyConfig
from sat.core.models import RecordedAction, ResolutionMethod
from sat.executor.strategies.base import ResolutionStrategy
from sat.services.dom_parser import DOMParser
from sat.services.ollama_embedding import OllamaEmbeddingService

logger = logging.getLogger(__name__)

# Minimum text-match score to trust the result without calling the embedding model.
_TEXT_MATCH_THRESHOLD = 0.90
# Strip emoji, arrows and whitespace noise from element text.
_NOISE_RE = re.compile(r'\s*[\u2190-\u21FF\u25A0-\u27FF\u2600-\u27BF\U0001F000-\U0001FFFF]+\s*')
_COLLAPSE_RE = re.compile(r'\s+')


class EmbeddingStrategy(ResolutionStrategy):
    """Resolves elements by comparing Ollama embeddings of DOM candidates."""

    method = ResolutionMethod.EMBEDDING

    def __init__(
        self,
        config: EmbeddingStrategyConfig,
        embedding_service: OllamaEmbeddingService | None = None,
        dom_parser: DOMParser | None = None,
    ) -> None:
        self._config = config
        self._svc = embedding_service or OllamaEmbeddingService(
            model=config.model,
            base_url=config.ollama_base_url,
            concurrency=config.concurrency,
        )
        self._dom = dom_parser or DOMParser()

    async def resolve(
        self, page: Page, action: RecordedAction
    ) -> tuple[ElementHandle | None, float | None]:
        # Scope to iframe when the action was recorded inside one
        root: Page | Frame = page
        if action.selector and action.selector.frame_url:
            root = _find_frame(page, action.selector.frame_url) or page

        # Extract DOM candidates
        candidates = await self._dom.extract_candidates(root, self._config.max_candidates)
        if not candidates:
            logger.debug("EmbeddingStrategy: no interactable candidates found")
            return None, None

        # ── Stage 1: fast text-match pass (no API call) ──────────────
        label = _extract_label(action)
        if label:
            scored = [
                (i, _text_score(label, c))
                for i, c in enumerate(candidates)
            ]
            scored = [(i, s) for i, s in scored if s > 0]
            if scored:
                scored.sort(key=lambda x: x[1], reverse=True)
                best_idx, best_score = scored[0]
                if best_score >= _TEXT_MATCH_THRESHOLD:
                    logger.debug(
                        "EmbeddingStrategy text match: score=%.4f  idx=%d  text=%.60s",
                        best_score, best_idx,
                        (candidates[best_idx].get("text") or "").strip()[:60],
                    )
                    element = await self._get_element_by_index(
                        root, candidates[best_idx]["index"]
                    )
                    return element, best_score

        # ── Stage 2: semantic embedding (handles form controls) ──────
        query = self._build_query(action)
        if not query:
            return None, None

        candidate_texts = [DOMParser.build_html_description(c) for c in candidates]

        all_texts = [query] + candidate_texts
        try:
            embeddings = await self._svc.embed_batch(all_texts)
        except Exception as exc:
            logger.error("Embedding batch failed: %s", exc)
            return None, None

        query_emb = embeddings[0]
        candidate_embs = embeddings[1:]

        ranked = self._svc.rank_candidates(query_emb, candidate_embs)
        if not ranked:
            return None, None

        best_idx, best_score = ranked[0]
        logger.debug(
            "EmbeddingStrategy embedding match: score=%.4f  idx=%d  desc=%.80s",
            best_score, best_idx, candidate_texts[best_idx],
        )

        if best_score < self._config.min_cosine_similarity:
            logger.debug(
                "EmbeddingStrategy: best embedding score %.4f < threshold %.4f",
                best_score, self._config.min_cosine_similarity,
            )
            return None, None

        element = await self._get_element_by_index(root, candidates[best_idx]["index"])
        return element, best_score

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_query(self, action: RecordedAction) -> str:
        """Build a structured semantic query matching the candidate format.

        Uses the same ``key=value`` notation as
        :meth:`DOMParser.build_html_description` so that cosine
        similarity between the query and the correct DOM candidate
        is maximised.
        """
        parts: list[str] = []
        s = action.selector

        # Tag name (e.g. 'input', 'button', 'a')
        if s and s.tag_name and s.tag_name != "unknown":
            parts.append(s.tag_name)

        # Placeholder — strongest signal for text inputs
        if s and s.placeholder:
            parts.append(f'placeholder="{s.placeholder}"')

        # Visible text — strongest signal for buttons/links
        if s and s.text_content:
            parts.append(f'text="{s.text_content}"')

        # ARIA label
        if s and s.aria_label:
            parts.append(f'aria-label="{s.aria_label}"')

        # Role
        if s and s.role:
            parts.append(f'role={s.role}')

        # Element id
        if s and s.id:
            parts.append(f'id={s.id}')

        # Input type
        if s and s.input_type:
            parts.append(f'type={s.input_type}')

        # Name attribute
        if s and s.name:
            parts.append(f'name={s.name}')

        # If we have structured parts, use them; otherwise fall back to
        # the raw CNL step text (still better than nothing).
        if parts:
            return " ".join(parts)

        # Fallback: CNL step text or outerHTML snippet
        if action.cnl_step:
            return action.cnl_step
        if s and s.outer_html_snippet:
            return s.outer_html_snippet
        return ""

    @staticmethod
    async def _get_element_by_index(page: "Page | Frame", index: int) -> ElementHandle | None:
        """Re-query the DOM to get a live ElementHandle by our captured index."""
        from sat.constants import INTERACTABLE_SELECTORS

        js = """
        (args) => {
            const [selectors, targetIdx] = args;
            let idx = 0;
            const seen = new Set();
            let found = null;
            document.querySelectorAll(selectors).forEach(el => {
                if (seen.has(el)) return;
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                const visible = s.display !== 'none' && s.visibility !== 'hidden'
                                && r.width > 0 && r.height > 0;
                if (!visible) return;
                seen.add(el);
                if (idx === targetIdx) found = el;
                idx++;
            });
            return found;
        }
        """
        try:
            handle = await page.evaluate_handle(js, [INTERACTABLE_SELECTORS, index])
            element = handle.as_element()
            return element
        except Exception as exc:
            logger.warning("Failed to get element by index %d: %s", index, exc)
            return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _extract_label(action: RecordedAction) -> str | None:
    """Return the cleanest human-readable label from the action.

    Priority order:
    1. ``selector.placeholder`` — for form controls the placeholder IS the label
    2. ``selector.text_content`` — visible button / link text
    3. ``selector.aria_label`` — screen-reader label
    4. ``cnl_step`` — raw CNL element_query as last resort
    """
    s = action.selector
    if s:
        if s.placeholder:
            return s.placeholder.strip()
        if s.text_content:
            return s.text_content.strip()
        if s.aria_label:
            return s.aria_label.strip()
    if action.cnl_step:
        return action.cnl_step.strip()
    return None


def _text_score(label: str, candidate: dict) -> float:
    """Score *candidate* by text/placeholder overlap with *label*.

    Returns a confidence in [0, 1]:
    - 1.00  exact case-insensitive match
    - 0.97  label == cleaned text (emoji/noise stripped)
    - 0.95  label is a prefix or suffix of text
    - 0.90  label is a substring of text (or vice-versa)
    - 0.00  no overlap
    """
    if not label:
        return 0.0

    label_n = label.strip().lower()

    # Collect all textual signals from the candidate
    raw_text = (candidate.get("text") or "").strip()
    clean_text = _COLLAPSE_RE.sub(" ", _NOISE_RE.sub("", raw_text)).strip().lower()
    placeholder = (candidate.get("placeholder") or "").strip().lower()
    aria = (candidate.get("ariaLabel") or "").strip().lower()

    best = 0.0
    for target in (raw_text.lower(), clean_text, placeholder, aria):
        if not target:
            continue
        if target == label_n:
            return 1.0
        if clean_text and clean_text == label_n:
            best = max(best, 0.97)
        if target.startswith(label_n) or target.endswith(label_n):
            best = max(best, 0.95)
        if label_n in target or target in label_n:
            best = max(best, 0.90)
    return best


def _find_frame(page: Page, frame_url: str) -> Frame | None:
    """Return the first child Frame whose URL matches *frame_url*, or None."""
    for frame in page.frames:
        if frame.url == frame_url:
            return frame
    # Looser match: one URL is a prefix of the other (handles trailing slashes, etc.)
    for frame in page.frames:
        if frame.url.startswith(frame_url) or frame_url.startswith(frame.url):
            return frame
    return None
