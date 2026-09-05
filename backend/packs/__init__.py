"""Business packs that ship with the framework (design §12.1).

A pack here imports ``kernos`` and ``ledger_core`` only — never a host — so a
different application can enable it by registering it with its kernel and handing
it the host services it asks for. ``tests/test_layering.py`` enforces the rule.
"""
