# Visual QA + impeccable critique — inferhub-watch dashboard (2026-09-07)

Three independent subagent assessments of https://leshchenko1979.github.io/inferhub-watch/
(1440 / 768 / 390 px, full-page screenshots + DOM/geometry/contrast measurement):

- **A — design critique** (impeccable assessment A): 8-dimension heuristic review.
- **B — defect scan** (impeccable assessment B): adversarial defect hunts, geometry + WCAG math.
- **C — visual QA**: checklist pass, measurement-verified, 4 vision false-positives explicitly disproven.

Screenshots: `/tmp/vqa/` (A: `critique-A/`, B: `critique-B/`, C: root + `metrics.json`).

Owner caption/axis contradiction re-verified in source: `site/board.py::scatter_section` —
`sx()` maps low price → left (no inversion), caption still says "Right = cheaper".

---

## MAJOR (cross-confirmed by ≥2 assessments)

| # | Finding | Evidence | Fix |
|---|---------|----------|-----|
| M1 | **Scatter caption contradicts the axis.** "Right = cheaper, up = smarter" — but right = pricier; best-value model sits far left. The page's core chart teaches the wrong reading. | C measured ticks ($0.0007 left → $0.0795 right); source-verified. Board's money law elsewhere: "cheaper = right". | Invert x-mapping so cheapest is right (matches caption + law), or flip caption. Inversion is the honest fix. |
| M2 | **Scatter illegible on mobile.** Fixed `viewBox="0 0 640 300"` scales to 358px → labels/ticks at **5.6 CSS px** (desktop 11.25px). Signature chart is decorative on the primary device. | C: computed `clientWidth/viewBox` math + zoom screenshots; A: 2× zoom crops, overlapping tiny labels in the expensive cluster. | Taller mobile viewBox (~390×420) with larger label type + staggered labels; or hide labels <720px and pair chart with the sorted cost table. |
| M3 | **Hover-only information.** Delta-color legend, "◂ cand" meaning, freshness threshold, sparkline scale — all `<title>` only (27+ spans); run-history grid has **no date header row**, each bar's date lives only in a `<title>`. Unreachable on touch. | B: DOM count; C: `has thead: False`, long-press unreliable. | Print the delta legend as visible text near the table; give history grid a real date header row; `aria-label` the rest. |
| M4 | **Mobile scroll containers with zero affordance, cutting content mid-word.** `.scroll` wrappers clip family-header chips (`…· bes` — the `· best` badge is off-canvas); scrollbars invisible on touch → reads as data loss. | B: scrollWidth 594 vs 358 visible; C: scroller 579 vs 358, screenshots. | Edge fade (`mask-image` gradient) or "scroll →" hint when `scrollWidth > clientWidth`; optionally let chips wrap <720px. |

## MINOR

| # | Finding | Assessments | Fix |
|---|---------|-------------|-----|
| m1 | Tap targets below floor: `core`/`cache` links 29×15 px, nav pills ~16px (44×44 recommended, 24×24 WCAG floor) | B, C | inline-block padding ≥24×24 on in-table anchors, pills |
| m2 | In-content links indistinguishable from body text (same gray, no underline) | B | teal tint (8.7:1 in palette) or underline |
| m3 | Legend between table and scatter is one ~15-line run-on paragraph defining the page's key terms — wall of prose at the most important real estate | A | convert to compact `<dl>`; define "IQ per $" at first use (hero tooltip) |
| m4 | Status line splits its timestamp on mobile: "2026-09-07" / "16:09:25 UTC" across lines | A, C | `white-space: nowrap` on the timestamp; short form `16:09 UTC` on mobile |
| m5 | Sub-12px text cluster on desktop: model-name `code` 10.56px (72×), `◂ cand` **8.64px**, spark labels 9–9.5px | C | raise floors: cand-mark ≥11px, names ≥12px |
| m6 | Scatter legend items wrap into vertical stacks ("not / in / use") | C | `flex-wrap` row layout / wider legend band |
| m7 | Missing `<h1>` (page starts at H2); method docs use H2 for Pass/Fail sub-blocks, same level as real sections | C | demote method sub-blocks to H3; add page H1 |
| m8 | Color-only encodings: history grid red-green (colorblind-hostile); rate-cell teal/amber mini-bars encode traffic basis, explained only in 11 hover titles | B, C | visible one-line legend for both |
| m9 | Delta tooltip text teaches the wrong rule: says "↓ teal cheaper, ↑ amber pricier", but rendered colors encode the in/out **series**, not direction (both deltas ↓, colored teal + amber) | C | rewrite tooltip to match actual encoding |
| m10 | Scatter last x-tick `$0.0795` clips its final glyph (text bbox 647.4 > viewBox 640) — *disputed: C measured label clipping clean, but tick geometry from B is specific* | B (C disputes for labels, not ticks) | `text-anchor: end` on last tick or shorten to `$0.08` |

## NITS

- Thousands-separator inconsistency: `46,200 IQ per $` vs `8908 req` (C).
- Orphaned "$" wrap on mobile ("6,700 IQ per $" breaks before `$`) (B).
- Sticky header ~150px desktop / 136px mobile (~16% of viewport) — never overlaps, but heavy on phones (C).
- "Zero-JavaScript" premise in docs slightly off — page ships a small progressive-enhancement script (C).
- `ali/qwen3.8-max` dot grazes its label tail (~3px) — disputed by C's 0-collision geometry pass (B).

## PASS (explicitly verified, no action)

- No horizontal page overflow at 1440/768/390; tables scroll inside deliberate wrappers only.
- Pricing table reflows to stacked cards on mobile — never crushed; long names intact.
- Hero verdict first fold, both viewports ("Route bulk here → cbcn/glm-5.3-flash → 46,200 IQ per $").
- Contrast passes everywhere measured (body 7.8:1, worst chip 4.6:1, all ≥ 4.5:1).
- Numeric columns: right-aligned, tabular-nums, decimals line up; prices consistently 4-dp.
- Scatter desktop: log axis (correct), direct point labels, collision-nudge pass works.
- Trust signals: freshness chip + UTC stamp + run cost + provenance + "N skipped" disclosure.

## Assessment A scores

Clarity 8 · Hierarchy 7 · Density 6 · Typography 7 · Color 7 · Data-viz 5 · Mobile 6 · Trust 9.
Verdict: "rare zero-JS dashboard that already gets the hard things right — loses points almost
entirely on mobile: the signature scatter and the explainer prose assume a 1440px monitor."

## Ranked fix order (effort/benefit)

1. **M1** — one-line inversion in `sx()` (honest fix, matches money law).
2. **M2** — mobile scatter re-layout (single media-query branch in the generator).
3. **M4 + M3** — scroll affordance + surface the trapped legend/date headers.
4. m4, m5, m9 — cheap text/CSS correctness fixes.
5. m1/m2/m3/m6/m7/m8 — polish batch.

*Report by 3 subagent assessments, synthesized + source-verified by ops session; no fixes applied (owner: report first).*

## Addendum — late first-batch receipts (verified first-hand, 2026-09-07)

The restart believed dead, three first-batch agents completed late. Their findings, cross-checked against source by the parent before inclusion:

### New MAJORs (source-confirmed)

- **MAJOR — family anchors hidden under sticky header.** CSS `style.css:154` targets `tr.fam-head[id^="fam-"]`, but `results.py:279` emits `class="fam-head"` on the `<tr>` while the `id="fam-…"` sits on the child `<th>` — the compound selector never matches, so fam rows get no `scroll-margin-top` (sections get it and land correctly; fam rows land at top 0, under the 150px header). Fix: extend the selector to also match `th[id^="fam-"]`, or add scroll-margin to `[id^="fam-"]` generally.
- **MAJOR — `&#215;` double-escape debris in Failures codes cells.** `board.py:667,685` build `&#215;` entity strings in Python; a second escape downstream turns them into `&amp;#215;`, rendering the literal text `&#215;` in 5 cells (4 in failures codes, 1 elsewhere). Fix: emit the literal `×` character and let the escaper run once.

### Additional findings adopted (from assessments A/B + late visual-QA)

- MINOR: scatter last x-tick `$0.0795` clips to `$0.079` at the SVG edge (desktop + mobile).
- MINOR: sparklines have no baseline rule — near-flat series float.
- MINOR: 375px — 182/459 tap targets <40px (viz bars, links), mitigated but below WCAG 2.5.8; table.pricing scrolls in its own wrapper (contained, page clean).
- MINOR (A): run-history grid has no visible time axis — dates only in per-cell tooltips; newest-end ambiguous (A's P1, schedule first among minors).
- MINOR (A): ticket sparkline in/out lines visually identical — docstring promises two-tone, both emit `s-line`.
- MINOR (A): sparkline tooltips are 316-char data walls (11 raw floats, no $, no in/out labels).
- MINOR (A): 1,426-char pricing caption is the sole definition site for load-bearing terms — fold into a "Reading guide" details.
- DESIGN (A): teal/amber carry triple duty (usage / price direction / price magnitude) on one screen — one legend line or shape differentiation would split the load.
- NITS: official-table last column flush right; `$0.000040` justified 6-dec exception; two `gpt-5.6-sol` dots nearly coincide; mobile probe-meta wraps mid-timestamp; Alternate line orphans `$`; freshness tooltip leaks `pricing.json` filename.
- DETECTOR (B): style source passes the AI-slop rule set 10/11 (2 uppercase+letter-spacing eyebrows flagged — intentional marks); reduced-motion + z-index scale clean; fonts loaded; 0 imgs without alt; 0 non-focusable clickables.

### Cross-assessment convergence

M1/M2 (scatter axis contradiction + mobile illegibility) were found independently by both the critique pair and the visual-QA pass — highest-confidence findings. The late pass separately confirmed price formatting is now uniformly 4-dec across the whole DOM, and no page-level overflow at any breakpoint.

**Late-batch verdict stands with the main report: NEEDS-WORK, fixes untouched (judge-only).**
