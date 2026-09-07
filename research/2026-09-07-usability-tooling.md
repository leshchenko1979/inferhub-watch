# Usability & layout assessment tooling — research (2026-09-07)

Question: what tools exist to assess layout and usability of inferhub-watch, and which fit
a zero-JS static dashboard rendered by a Python generator and deployed to GitHub Pages?

## Context that constrains the choice

- Site: 1 main board page + check page, plain HTML+CSS+~200 lines of progressive-enhancement JS.
  No client framework, no rehydration — that's pinned owner law.
- Data changes on every sweep (02:00Z cron + manual dispatches): prices, traffic, timestamps.
  Anything that pixel-diffs the live page nightly will false-positive every run.
- We already have: 496 pytest-style unit/render tests (byte-level HTML assertions),
  a frozen fixture corpus (Task 6) that renders deterministically, and a proven
  manual-QA pattern (visual-QA subagent with screenshots via the Mac CDP browser).
- CI runs on `ubuntu-latest` with `actions/deploy-pages`; Node 20+ is available both on the
  runner and on the agents box.
- Apple owner reads mostly from a phone → mobile-viewport checks matter more than desktop.

## Landscape (2026 research)

### Accessibility / semantic-layout scanners (CLI, CI-gateable)

| Tool | Engine | Fits? | Notes |
|---|---|---|---|
| **Pa11y CI** | axe-core or HTML_CodeSniffer | **Best fit** | Single command, URL list config, threshold gating, MIT. Static-DOM weakness is irrelevant for us (no client rendering). Cheap: ~2 min per 100 URLs. |
| axe CLI (`@axe-core/cli`) | axe-core | Good | Same engine, more per-node detail, slightly more setup. Zero-false-positive policy. |
| Lighthouse CI | axe subset | Good as trend, bad as gate | Score is a floor not a verdict; a 100 does not mean accessible. Useful for perf+SEO+best-practices trendline. 8-min CI setup. |
| WAVE | own | No | No CLI, no CI. Browser-extension only. |
| Pa11y default htmlcs runner | HTML_CodeSniffer | Only as second opinion | Noisier than axe; gate on axe results only. |

Research consensus (multiple 2026 benchmarks): automated a11y scanners catch only ~30–57% of
usability issues by volume; they gate the mechanical class (contrast, alt text, labels, heading
order, tap-target size) — not "does this layout make sense".

### Visual-regression tools (screenshot diff)

| Tool | Model | Fits? | Notes |
|---|---|---|---|
| **Playwright `toHaveScreenshot`** | pixel diff, in-repo baselines | **Best fit** | Free, no cloud, git-stored baselines, multi-viewport. Container-rule: baselines MUST be generated in the same image as CI (fonts/AA differ mac↔linux). |
| BackstopJS | pixel diff, self-hosted | OK alternative | JSON config, HTML diff report. Redundant if we adopt Playwright. |
| Percy / Chromatic / Applitools | cloud SaaS, AI diff | No | Paid tiers, DOM uploaded to third-party cloud, approval dashboards sized for teams — overkill for a 2-page personal dashboard. |

Key synergy for us: **render the site from the frozen fixture corpus inside CI** (fixtures
already exist from Task 6) → the rendered page is byte-deterministic → screenshot diffs are
stable with zero masking. Diffing the live page would fail nightly on data changes; diffing
the fixture render catches real layout regressions (CSS breaks, label collisions, overflow).

### Interactive/mobile-UX checks

- **Playwright** (same install) covers viewport matrix (375/768/1280), horizontal-overflow
  assertions (`document.scrollingElement.scrollWidth <= innerWidth`), tap-target size checks,
  anchor-scroll behaviour — all scriptable, all free.
- **Human-eyes pass stays mandatory**: our `site-visual-qa` subagent (screenshots + judgment)
  found 4 MAJOR issues the scanners cannot see (label collisions, tooltip overlap, touch-toggle).
  Keep it as the after-every-redesign pass; automate only the repeatable layer.

## Recommendation (stack, in adoption order)

1. **Pa11y CI (axe runner)** — `pa11yci.json` with the deployed pages URL, `threshold: 0`
   serious/critical violations. New small CI job or a step in the existing validate job.
   One-time cost ~30 min; guards contrast/labels/semantics forever after.
2. **Playwright visual suite on the fixture render** — generate `site/dist` from
   `tests/fixtures` in CI, screenshot desktop 1280 + mobile 375, `toHaveScreenshot` with
   baselines committed under `tests/visual/__snapshots__` (baselines generated in the
   `mcr.microsoft.com/playwright` container, never on the mac). Catches layout breaks that
   string-assertion tests miss.
3. **Lighthouse CI** — optional trendline only (perf/SEO/a11y score over time), never a gate.
4. **Visual-QA subagent** — unchanged, manual-trigger pass after UI redesigns.

Not adopted, pinned: SaaS visual platforms (cloud DOM upload, paid), WAVE (no CLI),
htmlcs gating (noise), whole-page pixel diff of the live page (data changes nightly).

Honest limit, stated once: automation covers the mechanical third of usability; the judgment
half (information density, reading order, "can Alexey find the answer in 5 seconds") stays
human/subagent territory.

## Integration sketch (not executed — awaiting owner word)

```yaml
# new job in watch.yml, after pages deploy
  usability:
    needs: pages
    runs-on: ubuntu-latest
    container: mcr.microsoft.com/playwright:v1.50.0-noble
    steps:
      - uses: actions/checkout@v4
      - run: pip install -q -r requirements.txt
      - run: python3 site/generate.py --fixtures   # fixture corpus → deterministic dist
      - run: npx -y pa11y-ci --config .pa11yci.json # axe gate on deployed URL
      - run: npx playwright test tests/visual/      # screenshot diff vs committed baselines
      - uses: actions/upload-artifact@v4
        if: failure()
        with: { name: visual-diffs, path: test-results/ }
```

New dependency class for the repo: Node/Playwright in CI (Python side untouched).
requirements.txt stays pure-Python; Playwright deps come from the container image.
