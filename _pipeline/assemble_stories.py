#!/usr/bin/env python3
"""
Phase 3 helper: assemble the build_digest stories JSON from a compact SELECTION
plus the agents' full return files — so story bodies never have to pass through
the orchestrator's context. The orchestrator picks candidate IDs off the bodiless
triage (intake_candidates.py); this script merges the picked candidates' full
records (body, regional, divergence, state_watch) from _pipeline/state/<date>/returns/
into the stories JSON that build_digest.py renders. It also emits the
history-entries JSON for append_history.py, since it already holds id, headline,
category, and core_topic for every shipped story.

SELECTION JSON shape (read from --in):
{
  "date": "YYYY-MM-DD",                 # required
  "framing": "2-3 sentence editor's note",
  "source_count": "18+",                # optional
  "stories": [
    {                                    # a picked agent candidate:
      "pick": "middle-east-c1",          #   candidate id from the intake triage
      "id": "s1",                        #   digest story id (s1.., oyr1.., wc1)
      "update_badge": "Update — …",      #   optional; else candidate's own value
      "overrides": {"headline": "…"}     #   optional; shallow-merged LAST — use to
    },                                   #   fix claim-attribution issues etc.
    {                                    # or an orchestrator-authored story
      "inline": {                        #   (used for On Your Radar cards):
        "id": "oyr1", "oyr": true,
        "category": "On Your Radar",
        "core_topic": "… (for history)",
        "headline": "…", "toc_hook": "…",
        "sources": ["…"], "body": ["…"]
      }
    }
  ]
}

Rules applied here (deterministic):
  * candidate topic "Wild Card" -> story gets "wildcard": true automatically
  * output order: OYR first, then regular (selection order), wild card last
  * hard errors (exit 1, problems JSON): unknown pick id, duplicate story id,
    picked candidate missing headline/toc_hook/body
  * soft warnings: Real-Estate body/State-Watch overlap tripwire — shared numbers
    or capitalized names between body and state_watch texts (the orchestrator
    should then read that candidate's return file and fix via overrides)

Usage:
  assemble_stories.py --in selection.json --today YYYY-MM-DD [--folder PATH]
      [--returns-dir DIR] [--out-stories PATH] [--out-history PATH]
Defaults: returns-dir/state paths under _pipeline/state/<today>/.
"""
import argparse, json, os, re, sys

_LEGACY_DEFAULT_FOLDER = os.path.expanduser("~/Daily news")  # portable last-resort only; real resolution is --folder > $DAILY_NEWS_FOLDER > parent of _pipeline/

CARRY_FIELDS = ("headline", "toc_hook", "sources", "body",
                "regional", "divergence", "state_watch")


def resolve_folder(arg=None):
    if arg:
        return arg
    if os.environ.get("DAILY_NEWS_FOLDER"):
        return os.environ["DAILY_NEWS_FOLDER"]
    try:
        return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    except NameError:
        return _LEGACY_DEFAULT_FOLDER


def topic_slug(topic):
    return re.sub(r"[^a-z]+", "-", (topic or "topic").lower()).strip("-")


def index_returns(returns_dir, problems):
    """Index every candidate by the same id scheme intake_candidates.py uses."""
    idx = {}
    if not os.path.isdir(returns_dir):
        problems.append(f"returns dir not found: {returns_dir}")
        return idx
    for fn in sorted(os.listdir(returns_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(returns_dir, fn)
        try:
            ret = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            problems.append(f"unreadable return file {fn}: {e}")
            continue
        topic = ret.get("topic")
        slug = topic_slug(topic)
        for i, cand in enumerate(ret.get("candidates") or []):
            if isinstance(cand, dict):
                idx[f"{slug}-c{i}"] = {"topic": topic, "cand": cand}
    return idx


_NUM_RE = re.compile(r"\d[\d,.]*%?")
# Lookahead so consecutive capitalized words yield every bigram ("More United Nations"
# -> "More United" AND "United Nations"), not just the first non-overlapping match.
_CAP_BIGRAM_RE = re.compile(r"\b(?=([A-Z][a-z]+ [A-Z][a-z]+)\b)")
_GENERIC_CAPS = {"State Watch", "United States", "Federal Reserve", "New York"}


def _flatten_text(x):
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return " ".join(_flatten_text(i) for i in x)
    if isinstance(x, dict):
        return " ".join(_flatten_text(v) for v in x.values())
    return ""


def overlap_tripwire(story):
    """Flag likely body/State-Watch overlap: shared multi-digit numbers or shared
    capitalized name bigrams. Cheap tripwire, not a semantic check — a hit means
    'read this one and fix', not 'certainly broken'."""
    sw = story.get("state_watch")
    if not sw:
        return []
    body_text = _flatten_text(story.get("body"))
    sw_text = _flatten_text(sw)
    hits = []
    body_nums = {n for n in _NUM_RE.findall(body_text) if len(re.sub(r"\D", "", n)) >= 2}
    sw_nums = {n for n in _NUM_RE.findall(sw_text) if len(re.sub(r"\D", "", n)) >= 2}
    shared_nums = body_nums & sw_nums
    if shared_nums:
        hits.append(f"numbers in both body and state_watch: {sorted(shared_nums)}")
    body_caps = set(_CAP_BIGRAM_RE.findall(body_text)) - _GENERIC_CAPS
    sw_caps = set(_CAP_BIGRAM_RE.findall(sw_text)) - _GENERIC_CAPS
    shared_caps = body_caps & sw_caps
    if shared_caps:
        hits.append(f"names in both body and state_watch: {sorted(shared_caps)}")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True, help="selection JSON")
    ap.add_argument("--today", required=True)
    ap.add_argument("--folder", default=None)
    ap.add_argument("--returns-dir", default=None)
    ap.add_argument("--out-stories", default=None)
    ap.add_argument("--out-history", default=None)
    args = ap.parse_args()

    folder = resolve_folder(args.folder)
    state_dir = os.path.join(folder, "_pipeline", "state", args.today)
    returns_dir = args.returns_dir or os.path.join(state_dir, "returns")
    out_stories = args.out_stories or os.path.join(state_dir, "stories.json")
    out_history = args.out_history or os.path.join(state_dir, "history-entries.json")

    problems, warnings = [], []
    try:
        sel = json.load(open(args.infile, encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"ok": False, "problems": [f"selection JSON unreadable: {e}"]}))
        sys.exit(1)

    date = sel.get("date")
    if date != args.today:
        warnings.append(f"selection date {date!r} != --today {args.today!r}")
    idx = index_returns(returns_dir, problems)

    stories, history_entries, seen_ids = [], [], set()
    for k, entry in enumerate(sel.get("stories") or []):
        if not isinstance(entry, dict):
            problems.append(f"stories[{k}] is not an object")
            continue
        if entry.get("inline"):
            story = dict(entry["inline"])
            core_topic = story.pop("core_topic", story.get("headline", ""))
        elif entry.get("pick"):
            pick = entry["pick"]
            hit = idx.get(pick)
            if not hit:
                problems.append(f"stories[{k}]: pick {pick!r} not found in {returns_dir} "
                                f"(known ids: {sorted(idx)[:12]}…)")
                continue
            cand, topic = hit["cand"], hit["topic"]
            story = {"id": entry.get("id"), "category": topic}
            if topic == "Wild Card":
                story["wildcard"] = True
            for f in CARRY_FIELDS:
                if cand.get(f) not in (None, "", []):
                    story[f] = cand[f]
            badge = entry.get("update_badge", cand.get("update_badge"))
            if badge:
                story["update_badge"] = badge
            if isinstance(entry.get("overrides"), dict):
                story.update(entry["overrides"])
            core_topic = cand.get("core_topic", "")
        else:
            problems.append(f"stories[{k}]: needs either 'pick' or 'inline'")
            continue

        sid = story.get("id")
        if not sid:
            problems.append(f"stories[{k}]: missing story id")
            continue
        if sid in seen_ids:
            problems.append(f"duplicate story id {sid!r}")
            continue
        seen_ids.add(sid)
        for f in ("headline", "toc_hook", "body"):
            if story.get(f) in (None, "", []):
                problems.append(f"story {sid}: missing {f!r}")
        for hit in overlap_tripwire(story):
            warnings.append(f"story {sid} overlap tripwire — {hit}")
        stories.append(story)
        history_entries.append({
            "id": sid,
            "headline": story.get("headline", ""),
            "category": story.get("category", ""),
            "core_topic": core_topic,
            "date": args.today,
        })

    n_wc = sum(1 for s in stories if s.get("wildcard"))
    if n_wc > 1:
        problems.append(f"{n_wc} wild-card stories selected; max 1")

    if problems:
        print(json.dumps({"ok": False, "problems": problems, "warnings": warnings},
                         indent=2, ensure_ascii=False))
        sys.exit(1)

    # Order: OYR first, then regular in selection order, wild card last.
    key = lambda s: 0 if s.get("oyr") else (2 if s.get("wildcard") else 1)
    order = sorted(range(len(stories)), key=lambda i: (key(stories[i]), i))
    stories = [stories[i] for i in order]
    history_entries = [history_entries[i] for i in order]

    out = {"date": args.today,
           "framing": sel.get("framing", ""),
           "stories": stories}
    if sel.get("source_count"):
        out["source_count"] = sel["source_count"]

    for path, payload in ((out_stories, out), (out_history, history_entries)):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    print(json.dumps({
        "ok": True,
        "stories": len(stories),
        "breakdown": {"oyr": sum(1 for s in stories if s.get("oyr")),
                      "regular": sum(1 for s in stories
                                     if not s.get("oyr") and not s.get("wildcard")),
                      "wildcard": n_wc},
        "out_stories": out_stories,
        "out_history": out_history,
        "warnings": warnings,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
