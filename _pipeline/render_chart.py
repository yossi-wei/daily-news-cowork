#!/usr/bin/env python3
"""
Renders a story's optional `figure` object into markup for the digest card.

Two renderers, chosen by figure["chart_type"]:

  bar   -> an HTML <div> grid, one row per point: label / proportional track / value.
           No y-axis, no ticks, no gridlines -- each bar carries its own number, which
           is what removes the entire axis apparatus. Because a bar's length is a CSS
           width percentage of max(values) from a zero origin, this renderer *cannot*
           express a truncated axis: the misleading-baseline failure mode is impossible
           by construction rather than forbidden by a rule someone has to remember.

  line  -> a minimal inline <svg> polyline. No tick algorithm; the first and last
           x-labels and the min/max y-values are labelled directly.

Deliberately imports nothing from the rest of the pipeline so it can be reviewed,
unit-tested and run standalone. Pure stdlib -- see README's no-third-party-packages
promise. url_key() below is a copy of the one in intake_candidates.py, kept by copy
rather than import to preserve that independence.

Usage (standalone, for eyeballing a rendering before it reaches a digest):
  render_chart.py --in figure.json --out /tmp/f.html [--urls a,b] [--color '#2563eb']
"""
import argparse
import html
import json
import math
import os
import re
import sys
from urllib.parse import urlparse

# --- Line-chart coordinate space -------------------------------------------------
# These are viewBox units, NOT pixels. The <svg> carries width="100%" and
# preserveAspectRatio, so the rendered width is whatever CSS gives it and these
# values never need to agree with any HTML dimension. What they do control is the
# aspect ratio, which is an editorial property rather than a layout one: a wide,
# short chart flattens slopes and a taller one exaggerates them. That is why
# `aspect` is exposed through config while the rest stay fixed.
VIEW_W = 720.0                 # coordinate-space width; only meaningful vs VIEW_H
DEFAULT_ASPECT = 4.0           # VIEW_W / VIEW_H — 4:1 reads honestly at card width
LINE_INSET = 28.0              # padding for the endpoint/extreme labels, in view units
AXIS_FONT_PX = 11              # must match .fig-line .fl-ax in template.html

DEFAULTS = {
    "min_points": 4,
    "max_points": 24,
    "max_label_chars": 18,
    "aspect": DEFAULT_ASPECT,
}

# Fallback only; real colors come from config.category_colors via build_digest.py.
FALLBACK_COLOR = "#2563eb"

FIGURE_CSS_RE = re.compile(
    r"[ \t]*/\* FIGURE-CSS-START \*/.*?/\* FIGURE-CSS-END \*/\n?", re.S)


def load_figure_css(folder=None):
    """Read the figure style rules out of template.html, which is their single source
    of truth. Used only by the standalone preview -- the digest itself gets them from
    build_digest.py, which reads the same markers. Returns '' if the template cannot
    be found, so the preview degrades to unstyled rather than failing."""
    if folder is None:
        folder = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(folder, "template.html"), encoding="utf-8") as fh:
            m = FIGURE_CSS_RE.search(fh.read())
        return m.group(0) if m else ""
    except OSError:
        return ""


def url_key(url):
    """Normalize a URL to domain + path (no scheme/query/fragment). Bare-domain /
    homepage URLs return '' -- too generic to establish that a chart's numbers came
    from an article the story actually cites."""
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return ""
    dom = (p.netloc or "").lower()
    if dom.startswith("www."):
        dom = dom[4:]
    path = (p.path or "").rstrip("/")
    if not dom or not path:
        return ""
    return dom + path


def _num(v):
    """True only for a real, finite number. Rejects bools (bool is an int subclass),
    NaN and +/-inf -- all of which survive a JSON round-trip and would otherwise
    produce silent garbage geometry."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return math.isfinite(v)


def fmt(v):
    """Locale-neutral number formatting: no thousands separators, no currency glyphs,
    no trailing '.0'. The unit is printed once as a caption, never glued to a value."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:g}"


def validate_figure(figure, story_urls=(), cfg=None):
    """Return (ok, reason). `reason` is a short phrase suitable for the run log."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if not isinstance(figure, dict):
        return False, "figure is not an object"
    if figure.get("kind") != "chart":
        return False, f"unsupported kind {figure.get('kind')!r}"
    ctype = figure.get("chart_type")
    if ctype not in ("bar", "line"):
        return False, f"unsupported chart_type {ctype!r}"

    series = figure.get("series")
    if not isinstance(series, list):
        return False, "series is not a list"
    n = len(series)
    if n < cfg["min_points"]:
        return False, f"{n} points, below min_points {cfg['min_points']}"
    if n > cfg["max_points"]:
        return False, f"{n} points, above max_points {cfg['max_points']}"

    labels, values = [], []
    for i, pt in enumerate(series):
        if not isinstance(pt, dict):
            return False, f"series[{i}] is not an object"
        lab, val = pt.get("label"), pt.get("value")
        if not isinstance(lab, str) or not lab.strip():
            return False, f"series[{i}] has an empty label"
        if len(lab) > cfg["max_label_chars"]:
            return False, f"series[{i}] label exceeds {cfg['max_label_chars']} chars"
        if not _num(val):
            return False, f"series[{i}] value is not a finite number"
        labels.append(lab)
        values.append(float(val))

    if max(values) == min(values):
        return False, "flat series (all values equal) carries no information"

    unit = figure.get("series_unit")
    if not isinstance(unit, str) or not unit.strip():
        return False, "series_unit is empty"
    if not isinstance(figure.get("alt"), str) or not figure["alt"].strip():
        return False, "alt is empty"

    # Traceability: the chart may not cite a source the story itself does not.
    src = figure.get("series_source")
    key = url_key(src) if isinstance(src, str) else ""
    if not key:
        return False, "series_source is missing or not a deep link"
    known = {url_key(u) for u in story_urls if isinstance(u, str)}
    known.discard("")
    if key not in known:
        return False, "series_source not among the story's cited urls"

    return True, None


def _parts(figure):
    labels = [pt["label"] for pt in figure["series"]]
    values = [float(pt["value"]) for pt in figure["series"]]
    return labels, values, figure["series_unit"]


def render_bar(figure, color=FALLBACK_COLOR):
    labels, values, unit = _parts(figure)
    e = html.escape
    hi = max(max(values), 0.0) or 1.0
    rows = []
    for lab, val in zip(labels, values):
        pct = max(val, 0.0) / hi * 100.0
        rows.append(
            '      <div class="fb-row">'
            f'<span class="fb-lab">{e(lab)}</span>'
            '<span class="fb-track">'
            f'<span class="fb-bar" style="width:{pct:.1f}%;background:{e(color)}"></span>'
            '</span>'
            f'<span class="fb-val">{e(fmt(val))}</span></div>'
        )
    return (
        f'    <div class="fig-bars" role="img" aria-label="{e(figure["alt"])}">\n'
        + "\n".join(rows) + "\n"
        f'      <div class="fb-unit">{e(unit)}</div>\n'
        '    </div>'
    )


def render_line(figure, color=FALLBACK_COLOR, uid="fig", cfg=None):
    labels, values, unit = _parts(figure)
    e = html.escape
    aspect = (cfg or {}).get("aspect", DEFAULT_ASPECT) or DEFAULT_ASPECT
    W, P = VIEW_W, LINE_INSET
    H = round(VIEW_W / float(aspect), 2)
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    x = lambda i: P + i * (W - 2 * P) / (n - 1)
    y = lambda v: H - P - (v - lo) / span * (H - 2 * P)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.5" fill="{e(color)}"/>'
        for i, v in enumerate(values)
    )
    tid = f"{uid}-t"
    return (
        f'    <svg class="fig-line" viewBox="0 0 {W:g} {H:g}" width="100%"\n'
        f'         preserveAspectRatio="xMidYMid meet" role="img" aria-labelledby="{e(tid)}">\n'
        f'      <title id="{e(tid)}">{e(figure["alt"])}</title>\n'
        f'      <polyline points="{pts}" fill="none" stroke="{e(color)}" stroke-width="2"\n'
        '                stroke-linejoin="round" stroke-linecap="round"/>\n'
        f'      {dots}\n'
        f'      <text class="fl-ax" x="{P:g}" y="{H - 6:g}">{e(labels[0])}</text>\n'
        f'      <text class="fl-ax" x="{W - P:g}" y="{H - 6:g}" text-anchor="end">{e(labels[-1])}</text>\n'
        f'      <text class="fl-ax" x="{P:g}" y="16">{e(fmt(hi))} {e(unit)}</text>\n'
        f'      <text class="fl-ax" x="{P:g}" y="{H - P + 14:g}">{e(fmt(lo))}</text>\n'
        '    </svg>'
    )


def render_figure(figure, story_urls=(), color=FALLBACK_COLOR, cfg=None, uid="fig"):
    """Validate then render. Returns (markup, None) on success, (None, reason) on
    refusal. Never raises on bad agent input -- a refused figure is a run-log line,
    not a failed digest."""
    try:
        ok, reason = validate_figure(figure, story_urls, cfg)
        if not ok:
            return None, reason
        caption = figure.get("caption")
        body = (render_bar(figure, color) if figure["chart_type"] == "bar"
                else render_line(figure, color, uid, {**DEFAULTS, **(cfg or {})}))
        if isinstance(caption, str) and caption.strip():
            body += f'\n    <p class="fig-caption">{html.escape(caption.strip())}</p>'
        return f'    <figure class="story-figure">\n{body}\n    </figure>', None
    except Exception as exc:  # defensive: never break the 5am run over a figure
        return None, f"renderer error: {type(exc).__name__}: {exc}"


def main():
    ap = argparse.ArgumentParser(description="Render a figure object standalone.")
    ap.add_argument("--in", dest="infile", required=True, help="figure JSON")
    ap.add_argument("--out", default=None, help="output HTML (default: stdout)")
    ap.add_argument("--urls", default="", help="comma-separated story urls for the traceability check")
    ap.add_argument("--color", default=FALLBACK_COLOR)
    args = ap.parse_args()

    figure = json.load(open(args.infile, encoding="utf-8"))
    urls = [u.strip() for u in args.urls.split(",") if u.strip()]
    if not urls:
        # Standalone convenience: trust the figure's own source so a rendering can be
        # eyeballed without hand-copying the story's url list.
        urls = [figure.get("series_source", "")]

    markup, reason = render_figure(figure, urls, args.color)
    if markup is None:
        print(json.dumps({"ok": False, "reason": reason}), file=sys.stderr)
        sys.exit(1)

    page = (
        "<!doctype html><meta charset='utf-8'><title>figure preview</title>\n"
        "<style>\n"
        "body{font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "background:#f8fafc;margin:0;padding:32px}\n"
        ".card{background:#fff;border-radius:8px;padding:8px 0;max-width:760px;margin:0 auto;"
        "box-shadow:0 1px 3px rgba(0,0,0,.08)}\n"
        + load_figure_css() +
        "</style>\n<div class='card'>\n" + markup + "\n</div>\n"
    )
    if args.out:
        open(args.out, "w", encoding="utf-8").write(page)
        print(json.dumps({"ok": True, "out": args.out, "bytes": len(page)}))
    else:
        sys.stdout.write(page)


if __name__ == "__main__":
    main()
