from __future__ import annotations

import html
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if str(_SITE) not in sys.path:
    sys.path.insert(0, str(_SITE))

import cellcopy  # noqa: E402
import mdhtml  # noqa: E402
import rundata  # noqa: E402
import tmpl  # noqa: E402
from probe.publishers import publisher_label  # noqa: E402
from probe.registry import load_aliases, load_registry  # noqa: E402

GITHUB = "https://github.com/leshchenko1979/inferhub-watch"
CLONE = (
    f'Clone <a href="{GITHUB}">leshchenko1979/inferhub-watch</a>, '
    "set <code>INFERHUB_API_KEY</code>, run <code>python3 -m probe.run</code>."
)
FONTS = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
    "&family=Newsreader:ital,opsz,wght@0,8..72,400;0,8..72,600;1,8..72,400&display=swap"
)
SECTIONS = (
    ("probe", "Latest results"),
    ("earlier", "Past runs"),
    ("method", "How we test"),
)


def base_href() -> str:
    raw = os.environ.get("PAGES_BASE", "/inferhub-watch").rstrip("/")
    return raw or ""


def load_runs() -> list[dict]:
    return rundata.load_runs(ROOT)


def alias_heading(alias: str, resolved: str) -> str:
    return (
        f'<th class="alias-cell" scope="row">'
        f'<span class="alias">{html.escape(alias)}</span>'
        f'<span class="pub">{html.escape(publisher_label(resolved))}</span>'
        "</th>"
    )


def board_nav() -> str:
    items = "".join(
        f'<li><a href="#{html.escape(sid)}">{html.escape(title)}</a></li>'
        for sid, title in SECTIONS
    )
    return tmpl.render("nav.html", items=items)


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
        js = (_SITE / "templates" / "board.js").read_text()
        script = f"<script>\n{js}</script>"
    return tmpl.render(
        "shell.html",
        title=html.escape(title),
        fonts=FONTS,
        css=html.escape(css),
        body_class=f' class="{html.escape(page_class)}"' if page_class else "",
        home=html.escape(home),
        page_nav=page_nav,
        crumb=crumb_html,
        body=body,
        footer=f"<footer><p>{CLONE}</p></footer>" if with_footer else "",
        script=script,
    )


def index_html(runs: list[dict], aliases: list[str], registry: list[dict]) -> str:
    if not runs:
        return shell("InferHub Watch", tmpl.render("empty.html"))

    latest = runs[-1]
    window = runs[-14:]
    score_ids = rundata.scoring_ids(registry)
    n_score = len(score_ids)
    specs = rundata.display_specs(registry)
    order = rundata.aliases_safe_first(aliases, latest, score_ids)
    rule = rundata.scoring_rule(score_ids)

    col_labels = [
        f"{rundata.run_stamp(run)} {rundata.origin_label(run)}" for run in window
    ]
    grid_rows = []
    for alias in order:
        cells = []
        for run, col_label in zip(window, col_labels):
            ok, total = rundata.scoring_pass_count(run, alias, score_ids)
            cls = "ok" if ok == total else ("mid" if ok else "bad")
            failed = rundata.scoring_failed_ids(run, alias, score_ids)
            title_parts = [col_label, f"{ok}/{total}"]
            if failed:
                miss = ", ".join(rundata.scoring_short(cid) for cid in failed)
                title_parts.append(f"missed: {miss}")
            else:
                title_parts.append("all pass")
            title = " · ".join(title_parts)
            cells.append(
                f'<td class="{cls}" data-tip="{html.escape(title)}"'
                ' tabindex="0"></td>'
            )
        resolved = rundata.resolved_for_alias(window[-1], alias, registry)
        grid_rows.append(f"<tr>{alias_heading(alias, resolved)}{''.join(cells)}</tr>")

    cmap = rundata.cell_map(latest)
    check_heads = []
    for spec in specs:
        scoring = bool(spec.get("scores_rank"))
        role = "scores" if scoring else "info · not ranked"
        kind = "col-score" if scoring else "col-info"
        check_heads.append(
            f'<th class="check-col {kind}" scope="col">'
            f'<a href="#check-{html.escape(spec["id"])}">'
            f"{html.escape(spec['title'])}</a>"
            f'<span class="th-role">{html.escape(role)}</span></th>'
        )
    matrix_rows = []
    safe = []
    for alias in order:
        resolved = ""
        check_tds = []
        for spec in specs:
            cell = cmap.get((alias, spec["id"])) or {}
            resolved = cell.get("resolved_model") or resolved
            status = cell.get("status") or "missing"
            summary = cellcopy.note({**cell, "check_id": spec["id"]})
            scoring = bool(spec.get("scores_rank"))
            data_label = html.escape(
                f"{rundata.scoring_short(spec['id'])} · "
                f"{'scores' if scoring else 'info'}"
            )
            if scoring:
                inner = (
                    f'<span class="pill">{html.escape(status)}</span>'
                    f'<p class="cell-note">{html.escape(summary)}</p>'
                )
            else:
                inner = f'<p class="cell-note info-note">{html.escape(summary)}</p>'
            check_tds.append(
                f'<td class="st-{html.escape(status)}" data-label="{data_label}">'
                f"{inner}</td>"
            )
        ok, total = rundata.scoring_pass_count(latest, alias, score_ids)
        row_attr = ' class="row-safe"' if total and ok == total else ""
        matrix_rows.append(
            f"<tr{row_attr}>{alias_heading(alias, resolved)}{''.join(check_tds)}</tr>"
        )
        if total and ok == total:
            safe.append(alias)

    explainers = []
    for spec in registry:
        brief = mdhtml.check_brief_html(ROOT, spec)
        explainers.append(
            f'<details id="check-{html.escape(spec["id"])}">'
            f"<summary>{html.escape(spec['title'])}</summary>"
            f'<div class="check-brief">{brief}</div></details>'
        )

    started_raw = (latest.get("started_at") or "")[:19]
    started = html.escape(started_raw.replace("T", " ") + " UTC")
    dispatch = (
        f'<p class="dispatch-meta">Last probe: '
        f'<time datetime="{html.escape(started_raw)}">{started}</time></p>'
    )
    if safe:
        rec = ", ".join(f"<code>{html.escape(a)}</code>" for a in safe)
        line = f"{rec}."
    else:
        line = "No alias is safe to use this run."
    recommend = (
        f'<div class="verdict"><h1>Safe to use</h1>'
        f'<p class="verdict-line">{line}</p></div>'
    )
    body = tmpl.render(
        "board.html",
        probe_title=section_title("probe"),
        earlier_title=section_title("earlier"),
        method_title=section_title("method"),
        recommend=recommend,
        dispatch=dispatch,
        check_heads="".join(check_heads),
        matrix_rows="".join(matrix_rows),
        n_score=str(n_score),
        score_label="check" if n_score == 1 else "checks",
        rule=rule,
        grid_rows="".join(grid_rows),
        explainers="".join(explainers),
        github=GITHUB,
        clone=CLONE,
    )
    return shell(
        "InferHub Watch",
        body,
        page_class="board",
        with_footer=False,
        page_nav=board_nav(),
    )


def check_page(spec: dict) -> str:
    md = (ROOT / "checks" / spec["id"] / "page.md").read_text()
    body = tmpl.render("check.html", article=mdhtml.md_to_html(md))
    return shell(
        spec["title"], body, crumb=spec["title"], nested=True, page_class="brief"
    )


def main() -> int:
    dist = ROOT / "site" / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    shutil.copy(ROOT / "site" / "style.css", dist / "style.css")
    aliases = load_aliases()
    registry = load_registry()
    runs = load_runs()
    (dist / "index.html").write_text(index_html(runs, aliases, registry))
    checks_dir = dist / "checks"
    checks_dir.mkdir()
    for spec in registry:
        (checks_dir / f"{spec['id']}.html").write_text(check_page(spec))
    print(dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
