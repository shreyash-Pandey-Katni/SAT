"""Strategy 2 — Semantic embeddings via Ollama.

Extracts all visible interactable elements from the current DOM, embeds them
alongside a query derived from CNL/selector info, and returns the best match
if cosine similarity >= min_threshold.
"""

from __future__ import annotations

import logging

from playwright.async_api import ElementHandle, Frame, Page

from sat.config import EmbeddingStrategyConfig
from sat.core.models import RecordedAction, ResolutionMethod
from sat.executor.strategies.base import ResolutionStrategy
from sat.services.dom_parser import DOMParser
from sat.services.ollama_embedding import OllamaEmbeddingService

logger = logging.getLogger(__name__)


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
        # Build semantic query
        query = self._build_query(action)
        if not query:
            return None, None

        # Scope to iframe when the action was recorded inside one
        root: Page | Frame = page
        if action.selector and action.selector.frame_url:
            root = _find_frame(page, action.selector.frame_url) or page

        # Extract DOM candidates
        candidates = await self._dom.extract_candidates(root, self._config.max_candidates)
        if not candidates:
            logger.debug("EmbeddingStrategy: no interactable candidates found")
            return None, None

        # Build text descriptions for each candidate
        candidate_texts = [DOMParser.build_html_description(c) for c in candidates]

        # Embed query + all candidates in parallel
        all_texts = [query] + candidate_texts
        try:
            embeddings = await self._svc.embed_batch(all_texts)
        except Exception as exc:
            logger.error("Embedding batch failed: %s", exc)
            return None, None

        query_emb = embeddings[0]
        candidate_embs = embeddings[1:]

        # Find best match
        ranked = self._svc.rank_candidates(query_emb, candidate_embs)
        if not ranked:
            return None, None

        best_idx, best_score = ranked[0]
        logger.debug(
            "EmbeddingStrategy best match: score=%.4f  idx=%d  html=%.80s",
            best_score, best_idx, candidate_texts[best_idx],
        )

        if best_score < self._config.min_cosine_similarity:
            logger.debug(
                "EmbeddingStrategy: best score %.4f < threshold %.4f",
                best_score, self._config.min_cosine_similarity,
            )
            return None, None

        # Resolve the Playwright ElementHandle for this candidate by its DOM index
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
