"""Materialize the bot's Cursor skills/rules into the agent workspace.

Cursor's headless bridge loads workspace guidance from ``.cursor/`` when
``LocalAgentOptions.setting_sources`` includes ``"project"``:
  - ``.cursor/rules/<name>.mdc`` with ``alwaysApply: true`` → loaded every turn.
  - ``.cursor/skills/<name>/SKILL.md`` → on-demand, description-triggered.
Source files live in ``app/agent_skills/`` and are copied idempotently before a turn.
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


def materialize(workspace: str) -> None:
    cursor = Path(workspace) / ".cursor"
    rules_src, skills_src = _SRC / "rules", _SRC / "skills"
    if rules_src.is_dir():
        for src in rules_src.glob("*.mdc"):
            _write(cursor / "rules" / src.name, _force_always_apply(src.read_text(encoding="utf-8")))
    if skills_src.is_dir():
        for skill_dir in skills_src.iterdir():
            if not skill_dir.is_dir():
                continue
            for f in skill_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(skills_src)
                    _write(cursor / "skills" / rel, f.read_text(encoding="utf-8"))
