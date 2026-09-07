"""Content-plane errors; the admin router maps them to HTTP statuses."""
from __future__ import annotations


class ContentError(Exception):
    status = 400


class NotFound(ContentError):
    status = 404


class Conflict(ContentError):
    """A state conflict: publishing a non-draft, editing a published version…"""
    status = 409


class PreconditionFailed(ContentError):
    """``If-Match`` did not match the current etag."""
    status = 412


class Invalid(ContentError):
    """A spec, patch or override that does not validate."""
    status = 422


class GateError(ContentError):
    """One or more publish gates failed. ``failures`` is a list of ``(gate, message)``."""
    status = 422

    def __init__(self, failures):
        self.failures = list(failures)
        super().__init__("; ".join(f"{g}: {m}" for g, m in self.failures))
