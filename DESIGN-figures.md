# Figures in the digest — architecture for the public release

Supersedes the proposal half of `RESEARCH-figures-in-digest.md`. That document's *findings*
still hold (no sandbox egress; `web_fetch` works; NASA/ESA license terms). Its *recommendations*
were written for a single-user install and two of them do not survive contact with a public
repo.

---

## What the repo constrains

Reading the tracked tree rather than assuming:

1. **`README.md` promises zero third-party packages** — *"python3 (standard library only — no
   third-party packages)"*. Every one of the eleven `_pipeline/*.py` scripts imports only
   stdlib. **This kills the matplotlib recommendation.** A clone-and-run project cannot grow a
   `pip install` step for a feature most users will leave off. Charts must be emitted as
   hand-written SVG from stdlib.

2. **Everything user-facing is config-driven and path-portable.** No absolute paths anywhere;
   folder resolution is `--folder` → `$DAILY_NEWS_FOLDER` → parent of `_pipeline/`. A figure
   feature that hardcodes hosts, beats, units, or date formats breaks that contract.

3. **Topics are wholly user-defined** — `key`, `slug`, `definition`, `enrichment`. The
   `enrichment` field selects a briefing fragment (`enrichment_none.md`,
   `enrichment_regional.md`, `enrichment_state_watch.md`) that `build_context.py` splices into
   the agent prompt. That is the established extension point and figures should use it rather
   than invent a second mechanism.

4. **`source_screen` already models "which domains do we trust"** with `aggregator_denylist`
   and `primary_domain_hints`. An image-host whitelist should mirror that shape and sit beside
   it, not duplicate the idea in a new vocabulary.

---

## The three architectural changes

### 1. Path B is the universal path; Path A is a specialization

The v1 doc sequenced charts first to reduce risk. For a public release the argument is
stronger than risk: **a chart is beat-agnostic and country-agnostic; an open-license image
whitelist is neither.**

My whitelist was NASA, ESA/Webb, ESA/Hubble, NOAA. That is excellent for *my* Space beat and
close to useless for a user whose topics are Health, Sports, and Local news — their equivalents
are WHO, CDC, Eurostat, ECB, a national statistics office, or nothing at all. Shipping my
whitelist as the default would quietly encode my beats as the project's beats.

A chart, by contrast, is built from numbers the story already cites. It works identically for a
Space user, a markets user, and a user tracking their city council's budget. So:

- **Path B (locally generated SVG charts) ships as the feature.** Default off, but complete,
  documented, and beat-neutral.
- **Path A (open-license image hotlinking) ships as a config-only capability with an empty
  default whitelist**, plus a README section explaining how to populate it and what the
  licensing bar is. The repo provides the mechanism and the safety rails; the user brings the
  hosts. My NASA/ESA list becomes an example in the docs, not a default in the code.

This also resolves a licensing question I glossed over. Curating a list of third-party media
hosts *in a public repo* is the repo making an implicit reuse claim on the user's behalf.
Empty-by-default puts that decision where it belongs.

### 2. Off by default, opt-in per topic

Two gates, both config:

```jsonc
// _pipeline/config.example.json — new top-level block
"figures": {
  "_comment": "Optional data figures in story cards. Off by default. 'chart' figures are generated locally as inline SVG from numbers the story already cites — no network, no license question. 'image' figures are remote hotlinks and require you to populate image_host_whitelist yourself; see README.",
  "enabled": false,
  "max_per_digest": 2,
  "max_images_per_digest": 1,
  "chart": {
    "min_points": 4,
    "max_points": 24,
    "max_label_chars": 18
  },
  "image_host_whitelist": [],
  "min_image_width_px": 800
}
```

```jsonc
// each topic object gains one optional field, mirroring "enrichment"
{ "key": "Science", "slug": "science", "definition": "…",
  "enrichment": "regional+divergence",
  "figures": "none" }          // "none" | "chart" | "chart+image"
```

Missing key means `"none"` everywhere. **A config written before this feature exists must
behave exactly as it does today** — that is the upgrade path for anyone already running the
digest, including me, and it should be an explicit test rather than an intention.

### 3. Briefing fragments, not prompt edits

`build_context.py` already splices `enrichment_*.md` per topic. Figures follow the identical
pattern with `briefing/figures_none.md` (a single "do not include figures" line),
`figures_chart.md`, and `figures_image.md`. `main.md` gets one `__FIGURES_BLOCK__` placeholder
next to the existing `__ENRICHMENT_BLOCK__`.

Consequence worth stating: **a topic with `figures: "none"` receives no figure instructions at
all**, so it costs zero tokens and has zero chance of an agent deciding today is special. The
default configuration's agent prompts are byte-identical to today's.

---

## The editorial gate, restated beat-agnostically

The v1 tests were already topic-neutral; the examples were not. Rewritten so they read the
same to a sports user and an astronomy user.

A figure qualifies only if it clears **all four**:

1. **Primary-artifact test — the figure *is* the news.** It depicts or plots the thing that
   happened: the data that was released, the image that was published, the measurement that was
   taken. Not people connected to the event, not the scene around it, not another instance of
   the same kind of thing. *(This is the test your "illustrative image of a ship" fails: it is
   a photo of a ship, not the ship.)*

2. **Irreducible-to-text test — it carries what two paragraphs cannot.** Legitimate answers:
   spatial structure, a shape over time, a distribution, a comparison across enough values that
   prose would become a list. If the honest answer is "it shows what I already wrote," it fails.
   A chart of two numbers fails — the sentence was the chart.

3. **Provenance test — the underlying source is one the story already cites.** For charts this
   is machine-checkable and should be enforced in code, not asked for in the prompt: the chart's
   `series_source` must appear in the story's own `urls`. For images, an open license from a
   whitelisted host.

4. **Legibility test — it survives digest scale.** Readable at ~700 px wide without zooming.
   Charts: 4–24 points, labels under ~18 characters. Images: ≥800 px source width.

### Auto-reject list

- Portraits, handshakes, podiums, anyone speaking
- Anything labelled *file*, *stock*, *illustrative*, *representative*, or *archive*
- Buildings, signage, or logos standing in for an institution
- Maps with no data plotted on them
- Charts under `min_points`, or with no units, or with unlabelled axes
- **Any number the agent estimated, interpolated, back-filled, or converted between units
  without the source doing so.** A fabricated point is worse than no figure, because a chart
  reads as authoritative.
- Artist concepts and AI-generated imagery, unless the rendering itself is the news
- Any host not on the user's whitelist

### Scarcity, enforced in code

An LLM asked "would a figure help?" answers yes nearly every time — each story looks better
with a picture in isolation, and a digest where everything is illustrated signals nothing. So
the cap is enforced in `build_digest.py` against `max_per_digest`, not requested in the prompt.
Ranking when over cap: **chart > image**, then by topic order. The briefing says, in these
words, *most days should have none*.

`why_it_qualifies` is required in the agent's return and rendered nowhere. Making the agent
name which criterion it cleared, in a field the reader never sees, leaves an audit trail in the
returns JSON and measurably suppresses reflexive attachment.

---

## Locale and portability details that only matter once strangers run it

- **Units come from the agent's `series_unit` string, never inferred.** No hardcoded `$`, `%`,
  or `bn`. The renderer prints the unit it is given, positioned as an axis label rather than
  glued to each number.
- **No locale-dependent number formatting.** Thousands separators and decimal marks are a
  config concern at most; default to plain `1234.5` rather than guessing `1,234.5` vs `1.234,5`.
- **Dates on a time axis use the label strings the agent supplies**, not a format string the
  renderer imposes — a user in `Europe/London` should not get US ordering.
- **Colors come from the story's existing `category_colors` entry**, so a figure inherits
  whatever palette the user configured rather than introducing a new one.
- **Text in SVG must be escaped.** Agent-supplied labels flow into `<text>` nodes; the existing
  `esc()` in `build_digest.py` covers the HTML cases and needs one addition for SVG context.

---

## Documentation surface

Three files need updating for the release, or the feature is invisible:

- **`README.md`** — a short *Figures (optional)* section under "How it works": what a chart
  figure is, that it is off by default, the four-test gate in two sentences, and the
  image-whitelist opt-in with the licensing bar spelled out. Reaffirm the stdlib-only promise
  explicitly, since "it draws charts" invites the assumption of a plotting dependency.
- **`setup-news-digest/SKILL.md`** — one interview question after Wild Card: *"Want the digest
  to draw a small chart when a story is genuinely about numbers? Off by default; most days have
  none."* If yes, set `figures.enabled` and ask which beats. Plus the corresponding
  `references/config.template.json` block.
- **`SKILL.md`** (orchestrator) — figures need no new phase, but the Phase 3 build step should
  note that the cap is enforced at render and a dropped figure is a log line, not a failure.

---

## What this does not change

`intake_candidates.py` triage stays bodiless — figure payloads travel with the candidate in the
returns file and never enter orchestrator context, same as `body`. `checkpoint.py` phases are
unchanged. No new run guard. The feature is additive at four well-defined points and inert when
`enabled` is false.
