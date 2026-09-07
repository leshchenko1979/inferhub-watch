"""Page chrome: shell wrapper, nav, shared table-cell builders.

Split out of generate.py (P2b section-module carve). Everything here is
presentation shared by more than one section; section-specific HTML lives
in its own module (spend/ticket/board/scatter/evidence/results).
"""
from __future__ import annotations

import html
import os
from pathlib import Path

import rundata
import tmpl
from probe.registry import load_aliases, repo_root  # noqa: F401 — re-exported

ROOT = repo_root()
SITE_DIR = Path(__file__).resolve().parent

GITHUB = "https://github.com/leshchenko1979/inferhub-watch"
CLONE = (
    f'Clone <a href="{GITHUB}">leshchenko1979/inferhub-watch</a>, '
    "set <code>INFERHUB_API_KEY</code>, run <code>python3 -m probe.run</code>."
)
FONTS = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
    "&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
)
SECTIONS = (
    ("pricing", "Cost per M tokens"),
    ("results", "Probe results"),
    ("method", "How we test"),
)

_BOARD_JS: list[str] = []

def _board_js() -> str:
    """board.js source, read once per process (rendered into every board
    page shell; re-reading per call was a per-render disk hit)."""
    if not _BOARD_JS:
        _BOARD_JS.append((SITE_DIR / "templates" / "board.js").read_text())
    return _BOARD_JS[0]


def base_href() -> str:
    raw = os.environ.get("PAGES_BASE", "/inferhub-watch").rstrip("/")
    return raw or ""


def check_href(fname: str, nested: bool = False) -> str:
    """URL for a check page, resolving the PAGES_BASE-vs-relative fallback
    once (was re-implemented at every drill-down call site, review R6)."""
    base = base_href()
    return f"{base}/checks/{fname}" if base else f"../checks/{fname}" if nested else f"checks/{fname}"


def results_available(runs: list[dict] | None = None) -> bool:
    """True when the latest run renders any model group (board or audition).
    Callers holding already-loaded runs pass them in; the default re-loads
    from disk (one-off callers, tests)."""
    if runs is None:
        runs = rundata.load_runs(ROOT)
    if not runs:
        return False
    return bool(rundata.run_groups(runs[-1]))



def board_nav(runs: list[dict] | None = None) -> str:
    """Section + family nav for the board page. Callers holding
    already-loaded runs pass them in (avoids a third disk read per render);
    default loads from disk (tests, one-off callers)."""
    if runs is None:
        runs = rundata.load_runs(ROOT)
    items = []
    for sid, title in SECTIONS:
        if sid == "pricing" and not rundata.load_pricing(ROOT):
            continue
        if sid == "results" and not results_available(runs):
            continue
        items.append(
            f'<li><a href="#{html.escape(sid)}">{html.escape(title)}</a></li>'
        )
    # Family anchors: jump straight to a family band inside #results.
    families = []
    aliases = load_aliases()
    for run in reversed(runs[-1:]):
        for group in rundata.run_groups(run):
            if rundata.incumbent_aliases(aliases, group["model"]) or group["routes"]:
                families.append(group["model"])
    if families:
        items.append(
            '<li class="nav-fams">'
            + " ".join(
                f'<a class="nav-fam" href="#fam-{html.escape(f)}">{html.escape(f)}</a>'
                for f in families
            )
            + "</li>"
        )
    return tmpl.render("nav.html", items="".join(items))


def section_title(section_id: str) -> str:
    return dict(SECTIONS)[section_id]


def shell(
    title: str,
    body: str,
    *,
    crumb: str = "",
    nested: bool = False,
    page_class: str = "",
    page_nav: str = "",
    header_meta: str = "",
    with_footer: bool = True,
) -> str:
    base = base_href()
    if base:
        home = f"{base}/"
        css = f"{base}/style.css"
    elif nested:
        home = "../index.html"
        css = "../style.css"
    else:
        home = "./"
        css = "style.css"
    crumb_html = (
        f'<p class="crumb"><a href="{html.escape(home)}">InferHub Watch</a> / {html.escape(crumb)}</p>'
        if crumb
        else ""
    )
    script = ""
    if page_class == "board":
        script = f"<script>\n{_board_js()}</script>"
    return tmpl.render(
        "shell.html",
        title=html.escape(title),
        fonts=FONTS,
        css=html.escape(css),
        body_class=f' class="{html.escape(page_class)}"' if page_class else "",
        home=html.escape(home),
        page_nav=page_nav,
        header_meta=header_meta,
        crumb=crumb_html,
        body=body,
        footer=f"<footer><p>{CLONE}</p></footer>" if with_footer else "",
        script=script,
    )


def _viz_cell(
    label: str,
    bar_pct: float,
    color_cls: str,
    data_label: str = "",
    data_tip: str = "",
) -> str:
    """Value on top, a 4px bar underneath (Gatus-style)."""
    bar = (
        f'<i class="{color_cls}" style="width:{bar_pct:.0f}%"></i>'
        if bar_pct > 0
        else ""
    )
    attr = f' data-label="{html.escape(data_label)}"' if data_label else ""
    attr += f' data-tip="{html.escape(data_tip)}"' if data_tip else ""
    return (
        f'<td class="num viz"{attr}><span class="viz-val">{label}</span>'
        f'<span class="viz-bar">{bar}</span></td>'
    )


