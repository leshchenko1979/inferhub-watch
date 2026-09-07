"""Template rendering — Jinja2 (P2b).

Was string.Template ($var substitution) for the page chrome; now Jinja2.
Every variable the callers pass is either pre-escaped text or a pre-built
HTML fragment (chrome.shell escapes title/css/home/body_class, sections
render their own HTML), so templates receive trusted strings by design.
Templates declare intent per slot: {{ var }} renders the string as-is
(matching the old string.Template behavior), and a `|e` filter marks a
slot that wants escaping — used where a slot takes raw data.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(DIR),
    autoescape=False,  # callers pass pre-escaped / pre-rendered HTML
    keep_trailing_newline=True,
)


def render(name: str, **kwargs: str) -> str:
    return _env.get_template(name).render(**kwargs)
