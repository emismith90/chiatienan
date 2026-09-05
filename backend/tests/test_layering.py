"""The layering rule, as a test rather than a convention (design §0.1, §12.1).

Allowed import edges between the top-level packages under ``backend/``::

    app         → kernos, ledger_core, packs, app
    packs       → kernos, ledger_core, packs
    ledger_core → kernos, ledger_core
    kernos      → kernos

Anything else is a reverse edge: the framework learning about a host, or a
domain library learning about a business. Packages that do not exist yet are
skipped, so this passes before Phase 3 creates ``ledger_core`` and ``packs``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
LAYERS = ("kernos", "ledger_core", "packs", "app")
ALLOWED = {
    "kernos": {"kernos"},
    "ledger_core": {"kernos", "ledger_core"},
    "packs": {"kernos", "ledger_core", "packs"},
    "app": {"kernos", "ledger_core", "packs", "app"},
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _violations(layer: str) -> list[str]:
    root = BACKEND / layer
    if not root.is_dir():
        return []
    bad: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for target in _imports(path) & set(LAYERS):
            if target not in ALLOWED[layer]:
                bad.append(f"{path.relative_to(BACKEND)} imports {target}")
    return bad


@pytest.mark.parametrize("layer", LAYERS)
def test_no_reverse_import_edges(layer):
    assert _violations(layer) == []


def test_kernos_exists_and_is_a_package():
    assert (BACKEND / "kernos" / "__init__.py").is_file()
