"""A deliberately tiny prompt template language (design §0.2, plan Task 2.7).

Two constructs and nothing else:

* ``{{path.to.var}}`` — substitution. Lists join with ``", "``; ``None`` renders as ``""``.
* ``{{#if path}} … {{else}} … {{/if}}`` — nestable conditional. Truthy = not ``None``,
  not ``""``, not ``False``, not an empty list.

No loops, no expressions, no I/O. The variable set is **closed**: a name outside
it fails at render and at publish (gate 1), so a typo cannot ship silently.
"""
from __future__ import annotations

import re
from typing import Any

#: The variables every prompt template may use (documented on the plugin too).
ALLOWED_VARS = frozenset({
    "persona.handle", "persona.name", "persona.aliases", "persona.language",
    "sender.name", "sender.member_id",
    "today", "space.id",
})

_TOKEN = re.compile(r"\{\{\s*(#if\s+([\w.]+)|else|/if|([\w.]+))\s*\}\}")


class TemplateError(ValueError):
    pass


def _lookup(path: str, variables: dict) -> Any:
    node: Any = variables
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _truthy(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, tuple, dict)):
        return len(value) > 0
    return True


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def used_variables(body: str) -> set[str]:
    return {m.group(2) or m.group(3) for m in _TOKEN.finditer(body) if m.group(2) or m.group(3)}


def validate(body: str, allowed: frozenset[str] = ALLOWED_VARS) -> list[str]:
    """Problems with ``body``: unknown variables and unbalanced blocks. Empty = fine."""
    problems = [f"unknown variable {v!r}" for v in sorted(used_variables(body) - allowed)]
    depth = 0
    for m in _TOKEN.finditer(body):
        tok = m.group(1)
        if tok.startswith("#if"):
            depth += 1
        elif tok == "/if":
            depth -= 1
            if depth < 0:
                problems.append("'{{/if}}' without '{{#if}}'")
                depth = 0
        elif tok == "else" and depth == 0:
            problems.append("'{{else}}' outside an '{{#if}}' block")
    if depth > 0:
        problems.append(f"{depth} unclosed '{{{{#if}}}}' block(s)")
    return problems


def render(body: str, variables: dict, *, allowed: frozenset[str] = ALLOWED_VARS) -> str:
    problems = validate(body, allowed)
    if problems:
        raise TemplateError("; ".join(problems))
    out, _ = _render(body, 0, variables, stop_at=None)
    return out


def _render(body: str, pos: int, variables: dict, *, stop_at: set[str] | None) -> tuple[str, int]:
    """Render from ``pos`` until a token in ``stop_at`` (``else``/``/if``) or the end.
    Returns the text and the position *after* the stopping token."""
    out: list[str] = []
    while True:
        m = _TOKEN.search(body, pos)
        if m is None:
            out.append(body[pos:])
            return "".join(out), len(body)
        out.append(body[pos:m.start()])
        tok = m.group(1)
        if tok.startswith("#if"):
            cond = _truthy(_lookup(m.group(2), variables))
            then_text, after = _render(body, m.end(), variables, stop_at={"else", "/if"})
            else_text = ""
            if body[after - 1] != "}" or _ended_with(body, after) == "else":
                else_text, after = _render(body, after, variables, stop_at={"/if"})
            out.append(then_text if cond else else_text)
            pos = after
        elif tok in ("else", "/if"):
            if stop_at and tok in stop_at:
                _LAST[0] = tok
                return "".join(out), m.end()
            raise TemplateError(f"unexpected '{{{{{tok}}}}}'")
        else:
            out.append(_text(_lookup(m.group(3), variables)))
            pos = m.end()


#: Which token ended the last `_render` call (`else` or `/if`); a tiny explicit
#: register beats threading a third return value through a 30-line renderer.
_LAST = [""]


def _ended_with(body: str, after: int) -> str:
    return _LAST[0]
