#!/usr/bin/env python3
"""
Phase 4 assembly: build the final digest HTML from a compact stories JSON +
the static template (_pipeline/template.html).

The orchestrator emits only story *content* as JSON; this script renders all the
repetitive HTML (cards, TOC, OYR section, footer, source bars) deterministically.
That keeps the model from re-reading a ~47 KB reference and re-emitting ~55 KB of
markup + ~10 KB of CSS every run — the bulk of the per-run token cost.

Pairs with validate_digest.py (run it on the output) and the same folder-resolution
convention as the other _pipeline helpers (--folder > $DAILY_NEWS_FOLDER > parent
of _pipeline/).

INPUT JSON shape (read from --in or stdin):
{
  "date": "YYYY-MM-DD",                  # required; drives filename + DIGEST_DATE
  "framing": "2-3 sentence editor's note (inline HTML like <strong> ok)",
  "source_count": "18+",                 # optional; shown in header subtitle
  "footer_sources": ["..."],             # optional; default = union of story sources
  "stories": [
    {
      "id": "s1",                        # s1.. regular, oyr1.. On Your Radar, wc1 wild card
      "oyr": false,                       # optional; true => On Your Radar card
      "wildcard": false,                  # optional; true => Wild Card (finance) card,
                                          #   additive like OYR, max 1, rendered after
                                          #   the regular stories with a 🃏 marker
      "category": "Middle East",          # canonical category label
      "headline": "...",
      "toc_hook": "one-line hook",
      "update_badge": "Update — Day 105", # optional; null/omit for none
      "sources": ["US Mission Vienna", "UK Government", "IAEA", ...],
      "source_bar": ["..."],             # optional; default = first 5 of sources
      "body": ["para 1", "para 2"],       # inline HTML (<em>/<strong>) preserved
      "regional": [                       # optional; omit/empty to skip the block
        {"region": "Iran", "outlet": "Tehran Times", "text": "... <em>..</em> .."}
      ],
      "divergence": "one-paragraph text", # optional; omit/null to skip
      "state_watch": [                    # optional; topics with "state-watch" enrichment
        {"state": "<code from config.focus_states>", "text": "..."}
      ]
    }
  ]
}

Usage:
  build_digest.py --in stories.json [--folder PATH] [--out FILE]
  cat stories.json | build_digest.py
"""
import argparse, datetime, json, os, re, sys

_LEGACY_DEFAULT_FOLDER = os.path.expanduser("~/Daily news")  # portable last-resort only; real resolution is --folder > $DAILY_NEWS_FOLDER > parent of _pipeline/

# Slugs for the two special sections. Topic slugs are loaded from config.topics at build
# time (see main); this dict is only the fixed fallback for the always-present sections.
CATEGORY_SLUG = {
    "On Your Radar": "oyr",
    "Wild Card": "wildcard",
}


def resolve_folder(arg=None):
    if arg:
        return arg
    env = os.environ.get("DAILY_NEWS_FOLDER")
    if env:
        return env
    try:
        return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    except NameError:
        return _LEGACY_DEFAULT_FOLDER


def esc(s):
    """Escape for text nodes/attributes. Body/regional/divergence text is treated
    as trusted inline HTML and is NOT escaped (model supplies <em>/<strong>)."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                  .replace('"', "&quot;"))


def slug_for(category):
    if category in CATEGORY_SLUG:
        return CATEGORY_SLUG[category]
    # Fallback for a category not declared in config: derive a stable kebab slug so the
    # class is never empty (color simply defaults if config has no matching color entry).
    return re.sub(r"[^a-z0-9]+", "-", str(category).lower()).strip("-")


def render_toc_entry(story, number):
    cat = story["category"]
    slug = slug_for(cat)
    if story.get("oyr"):
        num_html = '<span class="story-number toc-num">📌</span>'
    elif story.get("wildcard"):
        num_html = '<span class="story-number toc-num">🃏</span>'
    else:
        num_html = f'<span class="story-number toc-num">{number}</span>'
    return (
        f'      <a href="#{story["id"]}" class="toc-entry">\n'
        f'        {num_html}\n'
        f'        <span class="badge {slug}">{esc(cat)}</span>\n'
        f'        <span class="toc-hook">{esc(story.get("toc_hook",""))}</span>\n'
        f'      </a>'
    )


def render_source_bar(story):
    bar = story.get("source_bar") or story.get("sources", [])[:5]
    tags = "\n".join(f'      <span class="source-tag">{esc(s)}</span>' for s in bar)
    return (
        '    <div class="source-bar">\n'
        '      <strong>Sources:</strong>\n'
        f'{tags}\n'
        '    </div>'
    )


def render_regional(story):
    items = story.get("regional") or []
    if not items:
        return ""
    ps = []
    for i, it in enumerate(items):
        margin = "0 0 0" if i == len(items) - 1 else "0 0 10px"
        ps.append(
            f'      <p style="margin: {margin};">'
            f'<span class="region-tag">{esc(it["region"])}</span>'
            f'<span class="outlet">{esc(it["outlet"])}</span> {it["text"]}</p>'
        )
    return (
        '    <div class="regional">\n'
        '      <div class="regional-label">🌏 Regional Perspective — Beyond the Usual Sources</div>\n'
        + "\n".join(ps) + "\n"
        '    </div>'
    )


def render_divergence(story):
    d = story.get("divergence")
    if not d:
        return ""
    return (
        '    <div class="divergence">\n'
        '      <div class="divergence-label">⚡ Divergence Watch</div>\n'
        f'      {d}\n'
        '    </div>'
    )


def render_state_watch(story):
    items = story.get("state_watch") or []
    if not items:
        return ""
    states = " · ".join(esc(it["state"]) for it in items)
    ps = []
    for i, it in enumerate(items):
        margin = "0 0 0" if i == len(items) - 1 else "0 0 10px"
        ps.append(
            f'      <p style="margin: {margin};">'
            f'<span class="state-tag">{esc(it["state"])}</span> {it["text"]}</p>'
        )
    return (
        '    <div class="state-watch">\n'
        f'      <div class="state-watch-label">🏠 State Watch — {states}</div>\n'
        + "\n".join(ps) + "\n"
        '    </div>'
    )


def render_card(story, number, date):
    cat = story["category"]
    slug = slug_for(cat)
    classes = f"story-card {slug}"
    sources_attr = ", ".join(story.get("sources", []))

    # header meta row: number circle (regular only) + badge + optional update badge
    meta_bits = []
    if not story.get("oyr") and not story.get("wildcard"):
        meta_bits.append(f'        <span class="story-number">{number}</span>')
    meta_bits.append(f'        <span class="badge {slug}">{esc(cat)}</span>')
    if story.get("update_badge"):
        meta_bits.append(f'        <span class="update-badge">{esc(story["update_badge"])}</span>')
    meta_row = "\n".join(meta_bits)

    body_paras = "\n".join(f'      <p>{p}</p>' for p in story.get("body", []))

    blocks = [
        f'  <div id="{story["id"]}" class="{classes}"',
        f'       data-story-id="{date}-{story["id"]}"',
        f'       data-category="{esc(cat)}"',
        f'       data-sources="{esc(sources_attr)}">',
        '    <div class="story-header">',
        '      <div class="story-meta-row">',
        meta_row,
        '      </div>',
        '    </div>',
        f'    <h2 class="story-headline">{esc(story["headline"])}</h2>',
        render_source_bar(story),
        '    <div class="story-body">',
        body_paras,
        '    </div>',
    ]
    for extra in (render_regional(story), render_divergence(story), render_state_watch(story)):
        if extra:
            blocks.append(extra)
    blocks.append('  </div>')
    return "\n".join(b for b in blocks if b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default=None, help="stories JSON (default: stdin)")
    ap.add_argument("--folder", default=None)
    ap.add_argument("--out", default=None, help="output HTML path (default: <folder>/<date>-news-digest.html)")
    args = ap.parse_args()

    raw = open(args.infile, encoding="utf-8").read() if args.infile else sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"stories JSON is not valid JSON: {e}"}),
              file=sys.stderr)
        sys.exit(2)

    # --- validate required top-level fields ---
    missing_top = [f for f in ("date", "stories") if f not in data]
    if missing_top:
        print(json.dumps({"ok": False,
                          "error": f"stories JSON missing required fields: {missing_top}"}),
              file=sys.stderr)
        sys.exit(2)
    if not isinstance(data["stories"], list):
        print(json.dumps({"ok": False, "error": "'stories' must be a JSON array"}),
              file=sys.stderr)
        sys.exit(2)
    try:
        datetime.date.fromisoformat(data["date"])
    except (ValueError, TypeError) as e:
        print(json.dumps({"ok": False, "error": f"invalid 'date' value: {e}"}),
              file=sys.stderr)
        sys.exit(2)

    # --- validate per-story required fields; skip and warn on bad entries ---
    valid_stories, story_warnings = [], []
    for i, s in enumerate(data["stories"]):
        if not isinstance(s, dict):
            story_warnings.append(f"story[{i}] is not an object — skipped")
            continue
        missing = [f for f in ("id", "category", "headline") if not s.get(f)]
        if missing:
            story_warnings.append(
                f"story[{i}] missing required fields {missing} "
                f"(id={s.get('id')!r}) — skipped")
            continue
        valid_stories.append(s)
    if story_warnings:
        for w in story_warnings:
            print(f"WARNING: {w}", file=sys.stderr)
    data["stories"] = valid_stories

    date = data["date"]
    dt = datetime.date.fromisoformat(date)
    title_date = dt.strftime("%B %-d, %Y")
    header_date = dt.strftime("%A, %B %-d, %Y")

    folder = resolve_folder(args.folder)
    template = open(os.path.join(folder, "_pipeline", "template.html"), encoding="utf-8").read()

    # Branding + topic list come from config.json so the digest adapts to whoever
    # owns it. Fall back gracefully so a missing/broken config never aborts a build.
    cfg = {}
    try:
        cfg = json.load(open(os.path.join(folder, "_pipeline", "config.json"), encoding="utf-8"))
    except Exception:
        cfg = {}

    # Category → slug map is driven by config.topics so any topic set renders correctly.
    # The two special sections always map to their fixed slugs.
    if cfg.get("topics"):
        cat_slug = {t["key"]: t["slug"] for t in cfg["topics"] if t.get("key") and t.get("slug")}
        cat_slug.setdefault("On Your Radar", "oyr")
        cat_slug.setdefault("Wild Card", "wildcard")
        CATEGORY_SLUG.clear()
        CATEGORY_SLUG.update(cat_slug)

    # Per-category CSS (card border + badge colors) is generated from config.category_colors
    # and injected into the template, replacing what used to be hardcoded per-slug rules.
    colors = dict(cfg.get("category_colors") or {})
    colors.setdefault("oyr", {"border": "#475569", "badge_bg": "#f1f5f9", "badge_text": "#334155"})
    colors.setdefault("wildcard", {"border": "#0d9488", "badge_bg": "#ccfbf1", "badge_text": "#115e59"})
    _css = []
    for slug, cols in colors.items():
        border = cols.get("border")
        bg = cols.get("badge_bg")
        txt = cols.get("badge_text")
        if border:
            _css.append(f".story-card.{slug} {{ border-top-color: {border}; }}")
        if bg and txt:
            _css.append(f".badge.{slug} {{ background: {bg}; color: {txt}; }}")
    category_css = "\n  ".join(_css)

    stories = data.get("stories", [])
    oyr = [s for s in stories if s.get("oyr")]
    wildcards = [s for s in stories if s.get("wildcard") and not s.get("oyr")]
    regular = [s for s in stories if not s.get("oyr") and not s.get("wildcard")]

    # Wild card is additive and singular: keep the first, warn on extras.
    if len(wildcards) > 1:
        print(f"WARNING: {len(wildcards)} wildcard stories supplied; keeping the first "
              f"({wildcards[0].get('id')!r}) and dropping the rest", file=sys.stderr)
        wildcards = wildcards[:1]

    # number regular stories 1..N
    numbers = {}
    for i, s in enumerate(regular, 1):
        numbers[s["id"]] = i

    # --- TOC: OYR first, then regular, wild card last ---
    toc_lines = [render_toc_entry(s, None) for s in oyr]
    toc_lines += [render_toc_entry(s, numbers[s["id"]]) for s in regular]
    toc_lines += [render_toc_entry(s, None) for s in wildcards]
    toc_html = "\n".join(toc_lines)

    # --- OYR section ---
    if oyr:
        oyr_cards = "\n".join(render_card(s, None, date) for s in oyr)
        oyr_section = (
            '<div class="oyr-section">\n'
            '  <div class="oyr-header">📌 On Your Radar</div>\n'
            f'{oyr_cards}\n'
            '</div>\n'
        )
    else:
        oyr_section = ""

    # --- regular story cards ---
    stories_html = "\n\n".join(render_card(s, numbers[s["id"]], date) for s in regular)

    # --- Wild Card section (rendered after the regular stories, inside .content) ---
    if wildcards:
        wc_cards = "\n".join(render_card(s, None, date) for s in wildcards)
        stories_html += (
            '\n\n<div class="wildcard-section">\n'
            '  <div class="wildcard-header">🃏 Wild Card — from the financial wires</div>\n'
            f'{wc_cards}\n'
            '</div>\n'
        )

    # --- footer sources: explicit override or union of story sources (order preserved) ---
    if data.get("footer_sources"):
        footer_list = data["footer_sources"]
    else:
        seen, footer_list = set(), []
        for s in stories:
            for src in s.get("sources", []):
                if src not in seen:
                    seen.add(src)
                    footer_list.append(src)
    footer_html = "\n".join(f'      <span class="footer-source">{esc(s)}</span>' for s in footer_list)

    # --- header subtitle (topic list derived from config; falls back to none) ---
    n = len(regular)
    src_count = data.get("source_count", f"{len(footer_list)}+")
    topic_keys = [t.get("key") for t in cfg.get("topics", []) if t.get("key")]
    if topic_keys:
        sub = " &middot; ".join(esc(k) for k in topic_keys) + " &nbsp;&middot;&nbsp; "
    else:
        sub = ""
    sub += f"{n} stor{'y' if n == 1 else 'ies'}"
    if oyr:
        k = len(oyr)
        sub += f" + {k} on your radar"
    if wildcards:
        sub += " + a wild card"
    sub += f", synthesized from {src_count} sources"

    # Digest title: plain form for <title>, two-tone form (last word in <span>) for <h1>.
    digest_title = cfg.get("digest_title") or "Daily Digest"
    _tp = digest_title.rsplit(" ", 1)
    if len(_tp) == 2:
        digest_title_html = f"{esc(_tp[0])} <span>{esc(_tp[1])}</span>"
    else:
        digest_title_html = f"<span>{esc(digest_title)}</span>"

    out_html = (
        template
        .replace("__DIGEST_TITLE_HTML__", digest_title_html)
        .replace("__DIGEST_TITLE__", esc(digest_title))
        .replace("__CATEGORY_CSS__", category_css)
        .replace("__TITLE_DATE__", title_date)
        .replace("__HEADER_DATE__", header_date)
        .replace("__HEADER_SUB__", sub)
        .replace("__INTRO_FRAMING__", data.get("framing", ""))
        .replace("__TOC__", toc_html)
        .replace("__OYR_SECTION__", oyr_section)
        .replace("__STORIES__", stories_html)
        .replace("__FOOTER_SOURCES__", footer_html)
        .replace("__DIGEST_DATE__", date)
        .replace("__FEEDBACK_FILE__", esc(cfg.get("feedback_filename") or "news_feedback.md"))
    )

    out_path = args.out or os.path.join(folder, f"{date}-news-digest.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_html)
    print(json.dumps({
        "ok": True,
        "out": out_path,
        "bytes": len(out_html),
        "regular_cards": n,
        "oyr_cards": len(oyr),
        "wildcard_cards": len(wildcards),
        "footer_sources": len(footer_list),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
