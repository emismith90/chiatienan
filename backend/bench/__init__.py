"""Equivalence benchmark for the LLM engine.

`bench/` deliberately sits **outside** pytest's `testpaths` (which is
`["tests"]`), so nothing here runs in CI by accident — every entry point costs
real model calls. The tests that guard this package live in `tests/`, use stubs,
and stay offline.

Read `docs/superpowers/specs/2026-08-12-cursor-to-pi-harness-design.md` §11
before changing a grader: the harness exists to make "no behavior change" a
falsifiable claim, and a grader that cannot fail certifies nothing.
"""
