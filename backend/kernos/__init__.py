"""kernos — a portable kernel for running LLM agents on the Pi harness.

The framework layer of the Agent OS design
(``docs/superpowers/specs/2026-09-05-agent-cms-design.md``). It knows spaces,
principals, turns, profiles and plugins; it knows nothing about rooms, meals or
any host application. ``tests/test_layering.py`` enforces that: no module under
``kernos`` may import ``app``, ``packs`` or ``ledger_core``.
"""
__version__ = "0.9.0"

from kernos.packs import BasePack, DraftKind, PackError, PackRegistry, PackTool, ToolPack, apply_tool_overrides, compose_tools  # noqa: E402,F401
