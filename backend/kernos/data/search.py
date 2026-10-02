"""Search over a collection's documents: words (SQLite FTS5) and meaning (embeddings).

Pure helpers — what text a document is searched by, how it is folded, how a query becomes
an FTS5 expression, how two rankings are merged. :class:`kernos.data.DataStore` runs them.

* **Folding** lowercases and strips Vietnamese marks, ``đ`` included (NFD leaves it whole),
  on both the indexed text and the query, so ``pho`` finds ``Phở`` and ``dau`` finds ``đậu``.
* **Two texts per document.** FTS indexes the folded values; the embedding model reads the
  original, labelled by field (``name: Phở Hà``), because marks and labels carry meaning.
* **Reciprocal Rank Fusion** merges the two rankings by position only, never by score:
  BM25 and cosine are on different scales, and a rank needs no tuning per model.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from array import array

#: RRF's damping constant; 60 is the value from the original paper and the common default.
RRF_K = 60
#: How many hits each ranking contributes before the merge.
CANDIDATES = 50

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase, no marks, ``đ`` → ``d``: the form both index and query are compared in."""
    text = (text or "").lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def default_searchable(schema: dict) -> list[str]:
    """Every top-level string or string-array property, in schema order."""
    out = []
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type")
        if kind == "string" or (kind == "array" and (prop.get("items") or {}).get("type") == "string"):
            out.append(name)
    return out


def searchable_fields(collection: dict) -> list[str]:
    fields = collection.get("searchable")
    return default_searchable(collection["schema"]) if fields is None else list(fields)


def _values(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str) and v.strip()]
    return [value] if isinstance(value, str) and value.strip() else []


def labelled_text(fields: list[str], data: dict) -> str:
    """What the embedding model reads: ``field: value`` lines in the original script."""
    return "\n".join(f"{f}: {', '.join(vals)}" for f in fields if (vals := _values(data.get(f))))


def search_text(fields: list[str], data: dict) -> str:
    """What FTS5 indexes: the folded values, space-joined."""
    return " ".join(fold(v) for f in fields for v in _values(data.get(f)))


def query_tokens(query: str) -> set[str]:
    return set(_TOKEN_RE.findall(fold(query)))


def fts_query(query: str) -> str | None:
    """A safe FTS5 expression for free text: each folded token quoted (so no FTS syntax
    leaks through), OR-ed. ``None`` when the query has no word at all."""
    tokens = list(dict.fromkeys(_TOKEN_RE.findall(fold(query))))
    return " OR ".join(f'"{t}"' for t in tokens) if tokens else None


def content_hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def pack_vector(vector) -> bytes:
    """Unit-normalise and pack as float32, so cosine similarity is a plain dot product."""
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return array("f", (x / norm for x in vector)).tobytes()


def unpack_vector(blob: bytes) -> array:
    out = array("f")
    out.frombytes(blob)
    return out


def dot(a, b) -> float:
    return math.fsum(x * y for x, y in zip(a, b))


def rrf(*rankings: list) -> list[tuple]:
    """Merge rankings (lists of ids, best first) into ``[(id, [ranking indexes it came
    from]), …]``, best first. An id in both lists beats one that tops only one of them."""
    scores: dict = {}
    sources: dict = {}
    for which, ranking in enumerate(rankings):
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(key, []).append(which)
    order = sorted(scores, key=lambda k: (-scores[k], str(k)))
    return [(k, sources[k]) for k in order]
