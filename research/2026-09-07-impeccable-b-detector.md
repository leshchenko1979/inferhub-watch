# Impeccable Assessment B — deterministic detector results (inferhub-watch)

Date: 2026-09-07. Judge-only mechanical audit, no fixes. Detector = agent-run rule set
(the skill's bundled `detect.mjs` is not installed on this box).

Targets:
- Source: `/root/inferhub-watch/site/style.css` (= `site/dist/style.css`, byte-identical, 1320 lines), `site/templates/*.html`, `site/*.py`
- Live DOM: `https://leshchenko1979.github.io/inferhub-watch/` via Mac CDP tunnel (:9222), new tab, closed after. Viewports emulated: 1440×900 and 375×800 (`Emulation.setDeviceMetricsOverride`).

Method note: first-pass greps that used compound `a|b` patterns ran as literal strings
(grep tool regex=false default) and were invalid; every pattern below was re-verified
with `grep -E`. `sed` context reads confirm each classified hit.

## Part 1 — source scan (file:line)

| Rule | Hits | Locations | Verdict |
|---|---|---|---|
| border-left/right >1px used as accent (side-stripe) | 0 | only hit: `border-left: 1px solid var(--line)` style.css:1189 (1px hairline divider, .nav-fams) | PASS 0 |
| background-clip: text / -webkit-background-clip: text | 0 | — (CSS, templates, *.py) | PASS 0 |
| backdrop-filter / blur() in decorative cards | 0 | — | PASS 0 |
| border-radius >16px on cards/containers | 0 | values present: 2px (:438, :700, :708 — viz bars), 999px (:874 — .chip pill), 6px (:1238 — .tipbox) | PASS 0 |
| box-shadow blur ≥16px + border on same selector | 0 | only box-shadow: style.css:1239 `.tipbox` `0 4px 14px` (blur 14 < 16; selector has no border) | PASS 0 |
| repeating-linear-gradient | 0 | — | PASS 0 |
| linear-gradient grid-line overlays | 0 | — | PASS 0 |
| text-transform: uppercase + letter-spacing (eyebrow) | 2 selectors | `.mark` style.css:85–86 (ls 0.08em); `.pill` style.css:986–987 (ls 0.08em). DOM usage: `.mark` ×1 (header wordmark), `.pill` ×8 ("pill in-use"). `.cand-mark` :736 has neither — excluded | FAIL 2 |
| transition/animation on <img> or dot/label on hover | 0 on img/dot/letter of rule | `<img>`: 0 in templates/DOM; `.dot`/`.label` classes: do not exist. Transitions found: `a` color 180ms :191 (hover-coupled :194); `.timeline td::after` background 180ms :440 (dangling — no rule changes its bg on hover; only `td.tip-open` outline :1248). Hover rules: :145, :194, :1196, :1210 — all color-only. @keyframes: 0 | PASS 0 (letter) |
| @media (prefers-reduced-motion) | present | style.css:1036–1041 (`* { transition: none !important; animation: none !important; }`) | PASS |
| z-index values | semantic scale | `--z-sticky:1, --z-head:2, --z-link:3, --z-nav:4, --z-masthead:5` (:33–37); usages via var: :79 (masthead), :1065 (nav), :1230 (head). No arbitrary 999/9999. DOM computed z-index map: {2:1, 5:1} | PASS |

## Part 2 — live DOM checks (numbers)

| Rule | Hit count | Data / locations | Verdict |
|---|---|---|---|
| Elements with border-left/right-width > 1px | 0 | computed-style sweep of all elements | PASS 0 |
| Elements using background-clip: text | 0 | computed-style sweep | PASS 0 |
| Font stack applied (body + tables) | DATA | body/td/th computed font-family = `"IBM Plex Mono", ui-monospace, monospace`; td/th 12px. `document.fonts.status` = loaded; faces loaded: 3× IBM Plex Mono, 1× IBM Plex Sans (rest unloaded weights) | DATA |
| `<img>` without alt | 0 | `<img>` total = 0 (no images on page) | PASS 0 (trivial) |
| Interactive elements | DATA | a=48, button=0, [role=button]=0, a-without-href=0, tabindex≥0=112 (td[data-tip] tooltip triggers), non-focusable among clickables=0 | DATA |
| Color-contrast samples (computed pairs, ratio NOT computed per instruction) | DATA | body text `p.probe-meta`: color oklch(0.72 0.03 240) on oklch(0.17 0.028 255), 13px · th: oklch(0.94 0.015 240) on oklch(0.17 0.028 255), 12px · delta chip `span.chip.bad`: oklch(0.63 0.19 25) on oklch(0.21 0.032 255), 12px · td.num.viz: oklch(0.94 0.015 240) on oklch(0.17 0.028 255), 12px · main link: oklch(0.74 0.11 172) on oklch(0.17 0.028 255), 12px · `.dot`/`.label` samples: elements do not exist | DATA |
| Horizontal overflow, per top-level section | 1440px: 0 · 375px: 1 | 1440: all sections scrollW == clientW (body 1425/1425). 375: `table.pricing` scrollW 594 vs clientW 328 (+266, inside its own `.scroll` wrapper); body 360/360 — no page-level overflow; all other sections 0 | FAIL 1 (375) |
| Tap targets < 40px min-dimension at 375px | 182 of 459 | breakdown: [data-tip] 148/411 under 40 (e.g. svg 120×26 spark bars), a 34/48 (e.g. a.mark 114×18, a 29×15), .dot 0/0, .label 0/0, button 0/0, [role=button] 0/0 | FAIL 182 |
| Identical-structure repeated components (same class + same child signature) | DATA | td.num.viz ×25 (span.viz-val|span.viz-bar) · .route-drill ×13 (a|a) · .viz-val ×12 (span.pair-main|span.pair-alt) · .plumb ×12 (9 divs) · .alias-cell ×8 (span.alias|span.pub) · table.pricing ×4 · td.num ×5/×3 · .spend-stat ×3 · .evidence-item ×3 | DATA |

## Counts summary

- Source rules: **10 passed / 1 failed** (eyebrow: 2 selectors — `.mark`, `.pill`)
- DOM rules: **3 passed / 2 failed / 4 data-only** (fail: overflow@375 — 1 section; tap targets — 182/459 under 40px)

## Raw artifacts

- DOM JSON: `/tmp/cdp_audit_out.json`, `/tmp/cdp_followup.json`
- Driver scripts: `/tmp/cdp_audit.py`, `/tmp/cdp_follow.py` (raw CDP over the :9222 Mac tunnel; tab created + closed, receipts in session log)
