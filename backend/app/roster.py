"""Shim: this module moved to :mod:`ledger_core.roster` (plan Task 3.2). Every name it
defines — public and private — is re-exported so existing imports keep working."""
from ledger_core.roster import *  # noqa: F401,F403
import ledger_core.roster as _core

globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__")})
