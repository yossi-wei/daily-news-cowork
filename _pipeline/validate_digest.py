#!/usr/bin/env python3
"""
Phase 4 verification gate: structural QA on the generated digest HTML before it
ships. Catches the silent failures that matter for an unattended run.

Checks:
  * story-card count is within [min,max] (default 5-8), excluding On Your Radar
  * every TOC anchor (href="#id") resolves to an element with that id
  * every data-category value is a known category slug/label
  * the feedback IIFE is present (looks for DIGEST_DATE)
  * DIGEST_DATE constant equals the expected date
  * header story count, if present, matches the actual card count

Exit code 0 = pass, 1 = problems found (problems also printed as JSON).
Pure stdlib (regex-based) so it runs anywhere without bs4.

Usage:
  validate_digest.py --html FILE --date YYYY-MM-DD [--min 5] [--max 8]
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
    ap.add_argument("--min", type=int, default=5)
    ap.add_argument("--max", type=int, default=8)
    args = ap.parse_args()

    valid_categories, topics_loaded = load_valid_categories(resolve_folder(args.folder))

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
    if not (args.min <= n_regular <= args.max):
        problems.append(f"regular story count {n_regular} outside [{args.min},{args.max}]")
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
