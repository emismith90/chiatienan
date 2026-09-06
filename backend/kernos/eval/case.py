"""``EvalCase``: one benchmark case — a world to rebuild, then a single message to
replay (design §5.5). The fields are ``bench.corpus.Case``'s, JSON-serialisable, plus
``tags``/``review``. A case whose expectation a human has not confirmed is
``review: true`` and is never graded by a run."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

RECORD_VERSION = 1


@dataclass
class EvalCase:
    id: str
    source: str
    day: str
    actor: str
    members: list[dict] = field(default_factory=list)
    prior_steps: list[dict] = field(default_factory=list)
    message: str = ""
    history: str = ""
    images: list[dict] = field(default_factory=list)
    had_images: bool = False
    expect: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    review: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "EvalCase":
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def spec_sha(spec: Any) -> str:
    """The identity a run is keyed by: the stored spec **minus** ``eval`` (a threshold or
    suite-list edit must not invalidate a run of the thing under test — review F8).
    ``spec`` is a ``ProfileSpec`` (``stored()``) or its dict."""
    data = spec.stored() if hasattr(spec, "stored") else dict(spec)
    data = {k: v for k, v in data.items() if k != "eval"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
