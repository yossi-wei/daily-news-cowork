# Implementation plan — Path B (locally generated chart figures)

Scope: charts only. No remote images, no `<img>` tag anywhere in the output. Path A ships
later as a config-only capability per `DESIGN-figures.md`.

**Status: awaiting review. No code written.**

## Decisions taken (2026-08-03)

1. **Beats: AI & Tech and Space.** Everything else — Middle East, Quantum, Real Estate, Wild
   Card, On Your Radar — stays at `"none"`.

   *Correction:* an earlier draft of this line named "Technology, Science, and Business &
   Markets." Those are `config.example.json`'s topics, not the live config's. The real topic set
   is Middle East, AI & Tech, Space, Quantum, Real Estate, plus a Wild Card whose beat is
   finance/markets. Worth remembering when writing anything that ships in the public repo:
   **the example config and the live config have disjoint topic names**, and the plan must be
   written against the example while this deployment runs on the live one.
2. **`max_per_digest`: 2.**
3. **Renderer: CSS bars + minimal inline SVG line. No plotting library.** Rationale below.
4. Chart types in the first cut: `bar` and `line`.

---

## Goal

When a story is genuinely about a series of numbers it already cites, render those numbers as
an inline SVG chart inside the story card. Off by default. Zero new dependencies. When
`figures.enabled` is false, every byte of output and every byte of every agent prompt is
identical to today.

### Non-goals

- Remote images (Path A)
- Any chart type beyond `bar` and `line`
- Charts on the On Your Radar or Wild Card sections
- Changing triage, checkpointing, run guards, or history writeback

---

## The no-regression gate (verified, not assumed)

`build_digest.py` is deterministic. Rebuilding a shipped digest from its own `stories.json`
reproduces the file byte-for-byte:

```
2026-08-02: BYTE-IDENTICAL rebuild
2026-08-03: BYTE-IDENTICAL rebuild
```

So the acceptance test for "additive and inert" is exact rather than eyeballed: after every
step, rebuild the golden `stories.json` files and `diff` must be empty.

**Step 0, before touching anything:** copy the surviving `stories.json` + digest HTML pairs out
of `_pipeline/state/` into a scratch golden directory. Only the last `state_keep_days` (2) are
intact — everything older is zero-length, because cleanup truncates rather than unlinks on a
mounted folder. Capture 08-02 and 08-03 now or the harness is gone tomorrow.

---

## Renderer choice — why no library

### The runtime constraint eliminates most of the field

`pip install` is **blocked** in the Cowork sandbox — the proxy returns `403 Forbidden` on
pypi.org. The unattended 5 AM run executes `python3 _pipeline/build_digest.py` in that sandbox,
so it can use only what is preinstalled and nothing else, ever:

```
matplotlib PRESENT    numpy PRESENT    pandas PRESENT    PIL PRESENT
pygal -   plotly -   svgwrite -   drawsvg -   altair -   bokeh -   cairosvg -
```

So "pick a charting library" collapses to "matplotlib, or nothing." pygal — the one library
actually designed for standalone SVG output — is not installable here and never will be.

| Option | Available at 5 AM? | Code to maintain | Output | Verdict |
|---|---|---|---|---|
| **CSS bars + mini SVG line** | Always | **~31 lines** | 1.0–1.2 KB | **Chosen** |
| matplotlib | Today yes; not guaranteed on image refresh, and a real `pip install` for GitHub users | ~40 lines + SVG post-processing + rcParams theming | 9.9 KB | Rejected |
| pygal, drawsvg, svgwrite, altair, bokeh | **No — uninstallable** | — | — | Dead on arrival |
| Chart.js / Vega via CDN | Requires network *when the digest is opened* | ~15 lines | +200 KB inline, or a live CDN dependency | Rejected — breaks the self-contained file |
| QuickChart-style URL API | Remote hotlink | ~5 lines | — | Rejected — ships your data to a third party, plus link rot |

### The maintenance concern was right; the fix isn't a library

My ~280-line estimate was inflated because I specced a general cartesian chart engine:
nice-number tick algorithm, axis geometry, gridlines, label-collision thinning. **That
apparatus is the expensive part, and it is avoidable** — label each bar with its own value and
the entire y-axis disappears. Measured, working prototypes:

- **CSS bars** — 13 lines of Python, 7 lines of CSS, 1.2 KB of markup
- **Minimal SVG line** — 18 lines of Python, 1.0 KB, no ticks (first/last/min/max labelled)

~31 lines total against matplotlib's ~40 *plus* post-processing *plus* theming. **The
zero-dependency path is now the smaller-code path**, which resolves the concern in the
opposite direction from expected.

CSS bars are also better output than any image-based chart here: the labels and values are real
text, so they are selectable, searchable, screen-reader-native without aria scaffolding, and
they scale with the reader's browser font size. They inherit the digest's fonts and
`category_colors` automatically, print correctly, and have no viewBox scaling failure mode on a
phone.

And matplotlib does not actually save the styling work — its SVG arrives with an XML
declaration, a DOCTYPE, an RDF metadata block, and fixed `518.4pt × 216pt` dimensions that must
all be stripped for a responsive inline embed. You trade chart-geometry code for
post-processing-and-rcParams code, and gain a heavy dependency.

**The README promise stands** — not for purity, but because keeping it is now the cheaper
option. If the renderer ever grows past ~100 lines, revisit matplotlib as an optional import
with graceful degradation; that door stays open and costs nothing to leave open.

---

## Rejection logging

Every rejected figure is appended to the day's run log with the story headline, the failing
check, and the proposed series:

```
## Figures — 1 rendered, 3 rejected
- REJECTED "Fed holds rates steady" — flat series (all 6 values equal)
- REJECTED "Nvidia Q2 earnings" — series_source not in story urls
- REJECTED "Oil slips on inventory data" — 2 points, below min_points
```

After two weeks this is a labelled dataset of exactly the cases the gate is getting wrong,
which is how the auto-reject list gets its next entries. Cheap to build, and the only way the
gate improves on evidence rather than on my guesses about what should be excluded.

---

## Work breakdown

Five steps, each independently reviewable and each ending green on the gate above.

### Step 1 — `_pipeline/render_chart.py` (new, standalone)

The only new module, **~90 lines** of stdlib Python (revised down from 280 — see *Renderer
choice*). Deliberately has **no imports from the rest of the pipeline** so it can be reviewed,
unit-tested, and run as a CLI before anything is wired up.

Two renderers, both measured as working prototypes:

- **`bar`** → HTML `<div>` grid, one row per point: label / proportional track / value. No
  y-axis, no ticks, no gridlines — each bar carries its own number. 7 lines of CSS live in
  `template.html`. Bars are proportional to `max(values)` from a **zero origin**, which is
  structural rather than a rule to remember: a CSS width percentage cannot express a truncated
  axis, so the misleading-baseline failure mode is impossible by construction.
- **`line`** → minimal inline `<svg>` polyline, `viewBox="0 0 720 180"`, `width="100%"`. No
  tick algorithm; first and last x-labels and the min/max y-values are labelled directly.

**Public contract**

```python
validate_figure(figure, story_urls, cfg_chart) -> (ok: bool, reason: str|None)
render_chart(figure, colors, cfg_chart) -> str | None     # SVG markup, or None
```

`colors` is the story's existing `category_colors[slug]` entry, so a figure inherits the user's
palette instead of introducing one.

**`validate_figure` rejects on any of:**

| Check | Rule |
|---|---|
| type | `kind == "chart"`, `chart_type in ("bar","line")` |
| count | `min_points <= len(series) <= max_points` (default 4–24) |
| shape | every item has `label` (str) and `value` (int/float) |
| finiteness | `math.isfinite(value)` — catches NaN/inf from JSON round-trips |
| information | `max(values) != min(values)` — a flat series is not a chart |
| labels | non-empty, `<= max_label_chars` (default 18) |
| units | `series_unit` a non-empty string |
| **traceability** | `series_source` normalizes to a URL present in the story's own `urls` / `primary_source_url` |
| alt | `alt` non-empty |

The traceability check is the one worth calling out. It makes provenance test #3 from the
design doc a code gate rather than a prompt request: **the chart cannot cite a source the story
doesn't.** URL comparison normalizes scheme, `www.`, trailing slash, and query string — reuse
`url_key()` from `intake_candidates.py` (line ~336) rather than writing a second normalizer,
but by copy, to keep this module dependency-free.

**Rendering decisions**

- **No tick algorithm, no axis geometry, no label-collision logic** — the whole apparatus is
  designed out rather than implemented. Bars carry their own values; the line chart labels only
  its endpoints and extremes.
- **Bar zero-origin is structural, not a rule** — a CSS width percentage cannot express a
  truncated axis, so an automated pipeline physically cannot emit a misleading bar baseline.
  Line charts use a fitted baseline, which is legitimate for a trend.
- Values printed plain and locale-neutral — no thousands separators, no currency glyphs. The
  `series_unit` string appears once as a caption, never glued to each number.
- Label strings are used **verbatim**, so date ordering and format stay the user's. CSS grid
  handles truncation via `text-overflow: ellipsis`; no thinning code.
- Colors come from the story's `category_colors` entry, so figures inherit the user's palette.
- Accessibility: bars are real text and need no scaffolding; the SVG line gets `role="img"`,
  `<title>`, and `<desc>` from `alt`, with ids prefixed by the story id to avoid collisions.
- `html.escape()` on every agent-supplied string in both renderers. Labels are untrusted input,
  and the bar renderer emits HTML directly into the card.
- `@media print` rule so a printed digest keeps the bars (CSS backgrounds need
  `print-color-adjust: exact`).
- Fully deterministic — no timestamps, no randomness — so golden-file tests are stable.

**CLI for review:** `python3 _pipeline/render_chart.py --in fig.json --out /tmp/f.html` so you
can open a rendering and judge it before it is ever wired into a digest.

### Step 2 — Config and briefing (agents can emit; nothing renders yet)

1. **`_pipeline/config.example.json`** — add the `figures` block from `DESIGN-figures.md`
   (`enabled: false`), and add `"figures": "none"` to each example topic.
2. **`_pipeline/briefing/figures_none.md`** — deliberately *empty file*. Not a "do not include
   figures" sentence: mentioning figures in order to forbid them is what makes a model consider
   them. An empty splice keeps disabled prompts byte-identical to today's.
3. **`_pipeline/briefing/figures_chart.md`** (new) — the four-test gate, the auto-reject list,
   the "most days should have none" line, and the `figure` JSON sub-schema.
4. **`_pipeline/briefing/main.md`** — one `__FIGURES_BLOCK__` placeholder immediately after
   `__ENRICHMENT_BLOCK__`, and the optional `figure` object added to the documented return
   schema.
5. **`_pipeline/build_context.py`** — mirror the existing enrichment mechanism exactly. A
   `figures_tpl` dict beside `enrich_tpl` (line ~283), and one `.replace("__FIGURES_BLOCK__",
   …)` in the chain at line ~326, keyed on `topic.get("figures", "none")` and hard-gated on
   `cfg["figures"]["enabled"]`.

**Acceptance:** with the current config, regenerate prompts for a past date and diff against
the retained `state/<date>/prompts/*.md` — must be empty. That proves disabled costs zero
tokens and carries zero suggestion.

### Step 3 — Carry and render

1. **`_pipeline/assemble_stories.py`** — add `"figure"` to `CARRY_FIELDS` (line 54). *This is
   the silent-failure point: a field absent from that tuple is dropped between the agent return
   and the stories JSON with no error.*
2. **`_pipeline/build_digest.py`**
   - `import render_chart` (sibling module, same directory).
   - `render_figure(story, cfg)` helper alongside `render_regional` / `render_divergence` /
     `render_state_watch`, appended in the same `blocks` loop in `render_card()` (line ~204).
   - **Cap enforcement in `main()`, not in `render_card()`** — the cap is per digest, so it
     needs the whole story list. Before rendering, walk stories in order, validate each figure,
     keep the first `max_per_digest` that pass, and strip `figure` from the rest. Every drop
     emits a log line with the reason; **a rejected figure never fails the build.**
   - Skip figures on `oyr` and `wildcard` cards.
3. **`_pipeline/template.html`** — `figure` / `figcaption` CSS. The template currently contains
   zero `<img>` and zero `<svg>`, so this is purely additive: `margin: 4px 24px 16px`,
   `max-width: 100%`, caption in the existing muted source-bar style, and a `@media print`
   rule so a printed digest keeps the chart.

**Acceptance:** golden rebuild still byte-identical (no story has a `figure`), plus a synthetic
`stories.json` carrying one chart renders and opens correctly.

### Step 4 — Validator gates

**`_pipeline/validate_digest.py`** — three regex checks in the existing style:

- figure count ≤ `figures.max_per_digest` → **problem** (fails the run)
- zero `<img` tags in output → **problem** (Path B must not emit remote references; this is the
  guard that keeps Path A from leaking in early)
- every `<svg>` has a `role="img"` and a non-empty `<title>` → **problem**
- every `.fig-bars` block has a non-empty `aria-label` → **problem**

### Step 5 — Documentation

- **`README.md`** — a *Figures (optional)* subsection: what it does, off by default, the gate in
  two sentences, and an explicit restatement that this adds **no** third-party dependency, since
  "it draws charts" invites the opposite assumption.
- **`setup-news-digest/SKILL.md`** + `references/config.template.json` — one interview question
  after Wild Card: *"Want the digest to draw a small chart when a story is genuinely about
  numbers? Off by default; most days will still have none."* If yes, set `figures.enabled` and
  ask which beats get `"figures": "chart"`.
- **`SKILL.md`** (orchestrator) — one line in Phase 3 noting that figure caps are enforced at
  render and a dropped figure is a log line, not a failure.

---

## Test plan

**Unit (`render_chart.py`)**

- `nice_ticks` across ranges spanning several orders of magnitude, including all-negative and
  zero-crossing
- `validate_figure` accept/reject for each row of the table above
- Hostile labels: `<script>`, quotes, ampersands, RTL text, emoji, 200-character strings
- Degenerate series: 1 point, all-equal values, NaN, ±inf, all zeros
- Golden SVG for one bar and one line chart

**Integration**

- Synthetic `stories.json`: three stories, one valid figure, one figure whose `series_source`
  is not in the story's `urls` (must drop with a logged reason), one flat series (must drop)
- Over-cap: three valid figures with `max_per_digest: 2` → exactly two render, third logged
- Config with **no** `figures` key at all → builds, renders nothing, no exception. This is the
  upgrade path for anyone already running the digest.

**Regression (the gate)**

- Rebuild both golden `stories.json` files after each step; `diff` empty
- Regenerate agent prompts for a retained date; `diff` empty

**Visual**

- Open a digest with one chart at desktop and phone widths and confirm it reads at ~700 px
  without zooming — the legibility test is the one thing no unit test can assert.

---

## Risks and how each is handled

| Risk | Handling |
|---|---|
| Agent attaches a chart to everything | Cap enforced in code; `figures_none.md` empty so disabled beats never see the concept; `why_it_qualifies` required but never rendered |
| Agent invents or interpolates numbers | `series_source` must be one of the story's own cited URLs, checked deterministically. Does not catch a misread number — that stays a prompt-level rule and is the residual risk worth naming |
| Misleading truncated axis | Bar charts hard-baselined at zero in the renderer; not a prompt instruction |
| Label injection into markup | `html.escape()` on every agent string in both renderers; hostile-input unit tests. Higher stakes for bars, which emit HTML directly into the card |
| Silent field drop | `CARRY_FIELDS` change called out explicitly; integration test asserts a figure survives assemble |
| A bad figure breaks the 5 AM run | Validation failures drop the figure and log; never raise. Only the *validator's* count/`<img>`/`<title>` checks can fail a run, and those catch bugs in our own code rather than bad agent output |
| Golden fixtures expire | Snapshot in Step 0 — older `stories.json` are already zero-length |

---

## Open question

**The weak point on AI & Tech is vendor-supplied numbers.** A benchmark table or a funding
chart usually clears tests 1, 3, and 4 cleanly — the numbers *are* the announcement, they come
from a cited primary source, and they plot legibly. What the gate does not catch is that the
primary source is an interested party, and a chart of a vendor's own benchmark scores lends
marketing the visual authority of measurement.

This is the same problem the existing **claim-level sourcing** rule in `briefing/main.md`
already solves for prose: a load-bearing figure from a single interested source must be
attributed inline and kept out of the headline. Figures need the equivalent, and the natural
form is a required inline attribution in the caption rather than a new test — *"per Nvidia's
own benchmarks"* rendered as part of the figure, not buried in `confidence`.

Watch the rejection log for two weeks before deciding whether that becomes a hard rule or a
caption convention. Flagging now so it is a planned response rather than a surprise.
