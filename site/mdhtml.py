from __future__ import annotations

import html
import re
from pathlib import Path


def inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    return text


def md_to_html(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    para: list[str] = []
    list_items: list[str] = []
    fence: list[str] | None = None

    def flush_para() -> None:
        nonlocal para
        if para:
            body = inline(" ".join(para))
            out.append(f"<p>{body}</p>")
            para = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            items = "".join(f"<li>{inline(i)}</li>" for i in list_items)
            out.append(f"<ul>{items}</ul>")
            list_items = []

    for line in lines:
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith("```"):
                code = html.escape("\n".join(fence))
                out.append(f"<pre><code>{code}</code></pre>")
                fence = None
            else:
                fence.append(line)
            continue
        if stripped.startswith("```"):
            flush_para()
            flush_list()
            fence = []
            continue
        if not stripped:
            flush_para()
            flush_list()
            continue
        if stripped.startswith("# "):
            flush_para()
            flush_list()
            out.append(f"<h1>{inline(stripped[2:])}</h1>")
            continue
        if stripped.startswith("## "):
            flush_para()
            flush_list()
            out.append(f"<h2>{inline(stripped[3:])}</h2>")
            continue
        if stripped.startswith("### "):
            flush_para()
            flush_list()
            out.append(f"<h3>{inline(stripped[4:])}</h3>")
            continue
        if stripped.startswith("- "):
            flush_para()
            list_items.append(stripped[2:])
            continue
        para.append(stripped)
    flush_para()
    flush_list()
    if fence is not None:
        out.append(f"<pre><code>{html.escape(chr(10).join(fence))}</code></pre>")
    return "\n".join(out)


def check_brief_html(root: Path, spec: dict) -> str:
    md = (root / "checks" / spec["id"] / "page.md").read_text()
    lines = md.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    # Briefs render inside the board's "How we test" h3 — demote the md
    # h2 section heads to h4 so explanation headings sit BELOW the page's
    # table/section tiers in the outline (owner order 2026-09-07).
    return md_to_html("\n".join(lines)).replace("<h2>", "<h4>").replace("</h2>", "</h4>")
