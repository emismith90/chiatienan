"""Materialize the bot's Cursor skills/rules into the agent workspace.

Cursor's headless bridge loads workspace guidance from ``.cursor/`` when
``LocalAgentOptions.setting_sources`` includes ``"project"``:
  - ``.cursor/rules/<name>.mdc`` with ``alwaysApply: true`` → loaded every turn.
  - ``.cursor/skills/<name>/SKILL.md`` → on-demand, description-triggered.
Source files live in ``app/agent_skills/`` and are copied idempotently before a
turn, then anything stale is pruned — the workspace is on the persistent volume,
so a file left behind by an older build would keep being loaded indefinitely.
"""
from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).parent / "agent_skills"
_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _force_always_apply(text: str) -> str:
    m = _FM_RE.match(text)
    if not m:
        return f"---\nalwaysApply: true\n---\n{text}"
    lines = [ln for ln in m.group(1).splitlines()
             if not ln.strip().lower().startswith("alwaysapply:")]
    lines.append("alwaysApply: true")
    return "---\n" + "\n".join(lines) + "\n---\n" + text[m.end():]


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == data:
        return  # idempotent: unchanged → no rewrite
    path.write_text(data, encoding="utf-8")


def _prune(root: Path, keep: set[Path]) -> None:
    """Delete files under ``root`` that this build no longer ships.

    Now that the workspace lives on the mounted volume it outlives the
    container, so a renamed or deleted skill would otherwise keep being loaded
    forever — silently contradicting the one that replaced it. Empty
    directories go too, so a removed skill leaves no husk behind.
    """
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file() and path not in keep:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def materialize(workspace: str) -> None:
    cursor = Path(workspace) / ".cursor"
    rules_src, skills_src = _SRC / "rules", _SRC / "skills"
    written: set[Path] = set()
    if rules_src.is_dir():
        for src in rules_src.glob("*.mdc"):
            dest = cursor / "rules" / src.name
            _write(dest, _force_always_apply(src.read_text(encoding="utf-8")))
            written.add(dest)
    if skills_src.is_dir():
        for skill_dir in skills_src.iterdir():
            if not skill_dir.is_dir():
                continue
            for f in skill_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(skills_src)
                    dest = cursor / "skills" / rel
                    _write(dest, f.read_text(encoding="utf-8"))
                    written.add(dest)
    _prune(cursor / "rules", written)
    _prune(cursor / "skills", written)
