"""Text embeddings over OpenRouter's OpenAI-compatible ``/embeddings`` endpoint.

The data plane's only network dependency, and an optional one: a :class:`DataStore`
without an embedder searches by words alone, and one whose embedder fails says so in the
result (``semantic: "unavailable"``) and still returns the word hits.

``min_similarity`` travels with the model on purpose: cosine scores sit on a different
scale for every model (``gemini-embedding-001`` puts unrelated Vietnamese text near 0.5,
``bge-m3`` near 0.3), so a threshold only means something next to the model it was tuned on.
"""
from __future__ import annotations

import json
from typing import Callable
from urllib.request import Request, urlopen

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
#: Inputs per request; also bounds how long one search can spend embedding.
BATCH = 64


class EmbeddingError(RuntimeError):
    """The provider could not embed: network, auth, quota or a malformed reply."""


def _post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed host
        return json.loads(response.read().decode())


class OpenRouterEmbedder:
    def __init__(self, api_key: str, model: str, *, min_similarity: float,
                 post: Callable[..., dict] = _post, timeout: float = 30.0) -> None:
        self.model, self.min_similarity = model, min_similarity
        self._key, self._post, self._timeout = api_key, post, timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order. Raises :class:`EmbeddingError`."""
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            chunk = texts[start:start + BATCH]
            try:
                body = self._post(OPENROUTER_EMBEDDINGS_URL, {"model": self.model, "input": chunk},
                                  {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                                  self._timeout)
                rows = sorted(body["data"], key=lambda d: d["index"])
                vectors = [row["embedding"] for row in rows]
            except Exception as exc:  # noqa: BLE001 — every failure is the same to the caller
                raise EmbeddingError(f"{self.model}: {exc}") from exc
            if len(vectors) != len(chunk):
                raise EmbeddingError(f"{self.model}: {len(vectors)} vectors for {len(chunk)} inputs")
            out.extend(vectors)
        return out
