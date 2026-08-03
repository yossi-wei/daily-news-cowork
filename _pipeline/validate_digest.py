#!/usr/bin/env python3
"""
Phase 4 verification gate: structural QA on the generated digest HTML before it
ships. Catches the silent failures that matter for an unattended run.

Checks:
  * story-card count is within config.story_count [min,max], excluding On Your Radar
  * every TOC anchor (href="#id") resolves to an element with that id
  * every data-category value is a known category slug/label
  * the feedback IIFE is present (looks for DIGEST_DATE)
  * DIGEST_DATE constant equals the expected date
  * header story count, if present, matches the actual card count

Exit code 0 = pass, 1 = problems found (problems also printed as JSON).
Pure stdlib (regex-based) so it runs anywhere without bs4.

Usage:
  validate_digest.py --html FILE --date YYYY-MM-DD [--min N] [--max N]

--min/--max override config.story_count; omit them to use the configured range.
"""
import argparse, html as html_lib, json, os, re, sys

# The two always-present sections; topic categories are loaded from config.topics so the
# validator accepts whatever topic set the user configured.
SPECIAL_CATEGORIES = {"On Your Radar", "Wild Card"}


def resolve_folder(arg=None):
    if arg:
        return arg
    env = os.environ.get("DAILY_NEWS_FOLDER")
    if env:
        return env
    try:
        return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    except NameError:
        return os.path.expanduser("~/Daily news")


def load_story_count(folder, cli_min=None, cli_max=None):
    """Resolve the acceptable regular-card range: an explicit --min/--max wins, then
    config.story_count, then 5-8.

    SKILL.md documents this check as running against config.story_count, but the
    range used to come from argparse defaults that nothing overrode, so a user who
    configured a different story_count got silently validated against 5-8."""
    lo, hi, src = 5, 8, "built-in default"
    try:
        with open(os.path.join(folder, "_pipeline", "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        sc = cfg.get("story_count") or {}
        if isinstance(sc.get("min"), int) and isinstance(sc.get("max"), int):
            lo, hi, src = sc["min"], sc["max"], "config.story_count"
    except Exception:
        pass
    if cli_min is not None:
        lo, src = cli_min, "--min/--max"
    if cli_max is not None:
        hi, src = cli_max, "--min/--max"
    return lo, hi, src


def load_figure_cap(folder, default=2):
    """config.figures.max_per_digest, or a conservative default if unreadable."""
    try:
        with open(os.path.join(folder, "_pipeline", "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        cap = (cfg.get("figures") or {}).get("max_per_digest")
        return int(cap) if isinstance(cap, int) else default
    except Exception:
        return default


def check_figures(html, cap):
    """Structural checks on rendered figures. Returns a list of problems.

    These catch bugs in our own rendering rather than bad agent output -- a figure
    the agent got wrong is already refused at build time and logged. Anything caught
    here means the renderer or the cap logic misbehaved, which is worth failing a run
    over."""
    problems = []

    figures = re.findall(r'<figure[^>]*class="[^"]*\bstory-figure\b[^"]*"', html)
    if len(figures) > cap:
        problems.append(f"{len(figures)} figures rendered, above the configured "
                        f"max_per_digest of {cap}")

    # Path B is entirely local: charts are HTML/SVG built from cited numbers. An <img>
    # means a remote hotlink slipped in, which would leak the reader's IP to a third
    # party on open and break when the URL rots.
    imgs = re.findall(r"<img\b", html, re.I)
    if imgs:
        problems.append(f"{len(imgs)} <img> tag(s) in output; figures must be locally "
                        f"rendered, never remote references")

    # Accessible name on every figure. Bars are real text but the block is role="img",
    # so it needs a label; the line chart is an SVG and needs a non-empty <title>.
    for bar in re.findall(r'<div[^>]*class="[^"]*\bfig-bars\b[^"]*"[^>]*>', html):
        if not re.search(r'aria-label="[^"]+"', bar):
            problems.append("a fig-bars block has no non-empty aria-label")
    for svg in re.findall(r"<svg\b.*?</svg>", html, re.S):
        if 'role="img"' not in svg:
            problems.append("an <svg> figure is missing role=\"img\"")
        if not re.search(r"<title[^>]*>[^<]+</title>", svg):
            problems.append("an <svg> figure has no non-empty <title>")

    # Figure styling must be present exactly when a figure is. build_digest.py keeps or
    # strips the whole marked block, and the markers survive into the output, so testing
    # for the marker matches the actual mechanism -- sniffing for individual class names
    # would pass a partially-mangled block.
    has_css = "FIGURE-CSS-START" in html
    if figures and not has_css:
        problems.append("figures rendered but the figure CSS block was stripped")
    if not figures and has_css:
        problems.append("figure CSS present but no figure rendered (the block should be "
                        "stripped so a figures-off digest stays byte-identical)")
    if has_css and ".fb-track" not in html:
        problems.append("figure CSS block present but incomplete (.fb-track missing)")

    return problems


def load_valid_categories(folder):
    """Return (valid_set, loaded_topics_bool) from config.topics + the special sections."""
    valid = set(SPECIAL_CATEGORIES)
    loaded = False
    try:
        cfg = json.load(open(os.path.join(folder, "_pipeline", "config.json"), encoding="utf-8"))
        for t in cfg.get("topics", []):
            if t.get("key"):
                valid.add(t["key"])
                loaded = True
    except Exception:
        pass
    return valid, loaded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--folder", default=None)
    ap.add_argument("--min", type=int, default=None,
                    help="override the minimum regular-card count (default: config.story_count.min)")
    ap.add_argument("--max", type=int, default=None,
                    help="override the maximum regular-card count (default: config.story_count.max)")
    args = ap.parse_args()

    folder = resolve_folder(args.folder)
    valid_categories, topics_loaded = load_valid_categories(folder)
    count_min, count_max, count_src = load_story_count(folder, args.min, args.max)

    html = open(args.html, encoding="utf-8").read()
    problems, notes = [], []

    # --- element ids present in the document ---
    ids = set(re.findall(r'\bid="([^"]+)"', html))

    # --- story cards, split regular vs On Your Radar vs Wild Card ---
    card_open_re = re.compile(r'<div[^>]*class="[^"]*\bstory-card\b[^"]*"[^>]*>', re.I)
    cards = card_open_re.findall(html)
    oyr_cards = [c for c in cards if re.search(r'\boyr\b', c)]
    wildcard_cards = [c for c in cards if re.search(r'\bwildcard\b', c) and c not in oyr_cards]
    regular_cards = [c for c in cards if c not in oyr_cards and c not in wildcard_cards]
    n_regular = len(regular_cards)
    notes.append(f"regular story cards: {n_regular}; on-your-radar cards: {len(oyr_cards)}; "
                 f"wild-card cards: {len(wildcard_cards)}")
    notes.append(f"allowed regular-card range [{count_min},{count_max}] from {count_src}")
    if not (count_min <= n_regular <= count_max):
        problems.append(f"regular story count {n_regular} outside [{count_min},{count_max}]")
    if len(wildcard_cards) > 1:
        problems.append(f"{len(wildcard_cards)} wild-card cards found; at most 1 allowed")

    # --- TOC anchors resolve ---
    anchors = re.findall(r'<a[^>]*\bhref="#([^"]+)"', html)
    for a in anchors:
        if a not in ids:
            problems.append(f"TOC anchor #{a} has no matching element id")
    if anchors:
        notes.append(f"TOC anchors: {len(anchors)} (all resolve: {all(a in ids for a in anchors)})")

    # --- data-category values valid (against configured topics + special sections) ---
    if topics_loaded:
        for cat in re.findall(r'data-category="([^"]*)"', html):
            if html_lib.unescape(cat) not in valid_categories:
                problems.append(f"unknown data-category value: {cat!r}")
    else:
        notes.append("data-category check skipped (could not load config topics)")

    # --- feedback script + DIGEST_DATE ---
    m = re.search(r'DIGEST_DATE\s*=\s*["\']([^"\']+)["\']', html)
    if not m:
        problems.append("feedback script DIGEST_DATE constant not found")
    elif m.group(1) != args.date:
        problems.append(f"DIGEST_DATE is {m.group(1)!r}, expected {args.date!r}")

    # --- figures ---
    problems.extend(check_figures(html, load_figure_cap(folder)))

    # --- unreplaced template placeholders ---
    strays = list(set(re.findall(r'__[A-Z_]{3,}__', html)))
    if strays:
        problems.append(f"unreplaced template placeholders in output: {sorted(strays)}")

    # --- header count sanity (best-effort) ---
    hm = re.search(r'(\d+)\s+stor(?:y|ies)', html)
    if hm and int(hm.group(1)) != n_regular:
        notes.append(f"header mentions {hm.group(1)} stories but {n_regular} cards rendered "
                     f"(check this is intentional, e.g. excludes OYR)")

    result = {"ok": not problems, "problems": problems, "notes": notes}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if not problems else 1)

if __name__ == "__main__":
    main()
