"""OllamaEmbeddingService — local embedding via Ollama (nomic-embed-text, etc.).

Strategy:
  * Uses the Ollama Python async client to POST /api/embeddings.
  * Parallelises requests with an asyncio.Semaphore to avoid overloading Ollama.
  * Cosine similarity helper is included.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

import numpy as np
import ollama

logger = logging.getLogger(__name__)


class OllamaEmbeddingService:
    """Wraps Ollama's embedding endpoint for semantic element matching."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        concurrency: int = 8,
    ) -> None:
        self._model = model
        self._client = ollama.AsyncClient(host=base_url)
        self._semaphore = asyncio.Semaphore(concurrency)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> np.ndarray:
        """Embed a single text string. Returns a 1-D numpy array."""
        async with self._semaphore:
            resp = await self._client.embeddings(model=self._model, prompt=text)
        return np.array(resp["embedding"], dtype=np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed a list of texts in parallel (up to *concurrency* at once)."""
        tasks = [self.embed(t) for t in texts]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # Similarity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two vectors (returns scalar in [-1, 1])."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def rank_candidates(
        self,
        query_embedding: np.ndarray,
        candidate_embeddings: list[np.ndarray],
    ) -> list[tuple[int, float]]:
        """Return (index, score) pairs sorted by cosine similarity descending."""
        scores = [
            (i, self.cosine_similarity(query_embedding, emb))
            for i, emb in enumerate(candidate_embeddings)
        ]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True if Ollama responds and the model is available."""
        try:
            await self.embed("health check")
            return True
        except Exception as exc:
            logger.warning("Embedding health check failed: %s", exc)
            return False
