#!/usr/bin/env python3
"""
Phase 0 helper for the daily-news-digest orchestrator.

Deterministically builds the run context so story selection does not rely on the
model reading a 250+ entry JSON file by eye:

  * per-category dedup lists from the last N days (uses the `date` field, never
    `date_shown`; normalizes the messy category + id fields seen in real data),
    capped to the most-recent `dedup_inject_cap` entries per category to keep agent
    prompts small — with a per-category `dedup_omitted` count of older covered stories.
    The FULL window is still checked deterministically post-selection by
    intake_candidates.py, so capping the injected list does not weaken dedup.
  * the raw "Active preferences" (👎 rules) and "Liked stories" (👍 history) blocks
    from news_feedback.md
  * pending daily-topics entries dated today or earlier

Output: one JSON blob to stdout. Designed to never crash the digest run — on any
read/parse error for an optional input it emits an empty section plus a warning.

With --emit-agent-prompts it additionally stamps out the per-agent prompt files
deterministically (static briefing templates in _pipeline/briefing/ + per-topic
definition + dedup slice + feedback slice) into _pipeline/state/<date>/prompts/,
creates state/<date>/returns/ for the agents to write into, saves the full context
as state/<date>/context.json, and prints a COMPACT orchestrator summary instead of
the full context — so the orchestrator never holds the per-agent dedup lists in its
own context. The summary still carries `framing_ground_truth` (recent headlines) for
the categories in config `framing_check_categories`, because the orchestrator's
framing-consistency check needs those verbatim.

Usage:
  build_context.py [--folder PATH] [--today YYYY-MM-DD] [--window-days 30]
  build_context.py --today YYYY-MM-DD --emit-agent-prompts [--returns-root PATH]
"""
import argparse, json, os, re, sys
from datetime import datetime, timedelta

# Folder resolution is dynamic so the helper is portable (works on the user's
# Mac, in a sandbox, or if the tree is relocated). Order of precedence:
#   1. explicit --folder argument
#   2. $DAILY_NEWS_FOLDER environment variable
#   3. script location: the helpers live in "<digest folder>/_pipeline/", so the
#      digest folder is the parent of this file's directory.
# The old hardcoded macOS path is kept only as a last-resort fallback.
_LEGACY_DEFAULT_FOLDER = os.path.expanduser("~/Daily news")  # portable last-resort only; real resolution is --folder > $DAILY_NEWS_FOLDER > parent of _pipeline/

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

# Default injected-dedup cap if config.json is missing/unreadable. The FULL 30-day
# history is still checked deterministically post-selection (intake_candidates.py),
# so capping the injected list is a pure token saver, not a dedup weakening.
_DEFAULT_INJECT_CAP = 18

def load_window_days(folder, warnings):
    """Read dedup_window_days from config.json; fall back to 30."""
    path = os.path.join(folder, "_pipeline", "config.json")
    try:
        val = json.load(open(path)).get("dedup_window_days")
        if isinstance(val, int) and val > 0:
            return val
    except Exception as e:
        warnings.append(f"could not read dedup_window_days from config.json: {e}")
    return 30


def resolve_inject_cap(folder, arg, warnings):
    # Precedence: explicit --dedup-cap arg > config.json `dedup_inject_cap` > default.
    if arg is not None:
        return arg
    path = os.path.join(folder, "_pipeline", "config.json")
    try:
        cfg = json.load(open(path))
        val = cfg.get("dedup_inject_cap")
        if isinstance(val, int):
            return val
    except Exception as e:
        warnings.append(f"could not read dedup_inject_cap from config.json: {e}")
    return _DEFAULT_INJECT_CAP

# Canonical topic categories the digest covers. History data is messy, so map
# every observed variant onto one canonical label.
CATEGORY_ALIASES = {
    "ai": "AI & Tech",
    "ai & tech": "AI & Tech",
    "ai and tech": "AI & Tech",
    "tech": "AI & Tech",
    "middle east": "Middle East",
    "space": "Space",
    "quantum": "Quantum",
    "real estate": "Real Estate",
    "on your radar": "On Your Radar",
}

def canon_category(raw):
    if not raw:
        return "Uncategorized"
    return CATEGORY_ALIASES.get(str(raw).strip().lower(), str(raw).strip())

def story_id(entry):
    # Real data uses both `id` and `story_id`. Fall back to a synthesized key.
    return entry.get("id") or entry.get("story_id") or \
        f"{entry.get('date','?')}::{(entry.get('headline','') or '')[:60]}"

def build_dedup(folder, today, window_days, warnings):
    path = os.path.join(folder, "story-history.json")
    out = {}
    try:
        with open(path) as f:
            stories = json.load(f).get("stories", [])
    except Exception as e:
        warnings.append(f"could not read story-history.json: {e}")
        return out
    cutoff = today - timedelta(days=window_days)
    for s in stories:
        d = s.get("date")
        if not d:
            continue
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            continue
        if dt < cutoff or dt > today:
            continue
        cat = canon_category(s.get("category"))
        out.setdefault(cat, []).append({
            "id": story_id(s),
            "headline": s.get("headline", ""),
            "core_topic": s.get("core_topic", ""),
            "date": d,
        })
    for cat in out:
        out[cat].sort(key=lambda x: x["date"], reverse=True)
    return out

def _feedback_section(folder, heading_re, warnings):
    """Extract one '## <heading>' section of news_feedback.md up to the next '## '."""
    path = os.path.join(folder, "news_feedback.md")
    try:
        text = open(path).read()
    except Exception as e:
        warnings.append(f"could not read news_feedback.md: {e}")
        return ""
    m = re.search(r"##\s*" + heading_re + r"\s*(.*?)(?:\n##\s|\Z)", text, re.S)
    if not m:
        return ""
    block = m.group(1).strip()
    # Treat the "no entries yet" placeholder as empty.
    if block.lower().startswith("*(no entries"):
        return ""
    return block

def build_feedback(folder, warnings):
    return _feedback_section(folder, r"Active preferences", warnings)

def build_liked_history(folder, warnings):
    # 👍 history: positive patterns that weight story selection (esp. the Wild Card).
    return _feedback_section(folder, r"Liked stories[^\n]*", warnings)

def build_pending_topics(folder, today, warnings):
    path = os.path.join(folder, "daily-topics.md")
    try:
        text = open(path).read()
    except Exception as e:
        warnings.append(f"could not read daily-topics.md: {e}")
        return []
    m = re.search(r"##\s*Pending\s*(.*?)(?:\n##\s|\Z)", text, re.S)
    if not m:
        return []
    pending = []
    line_re = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\[[^\]]+\]|\S+)\s*\|\s*(.+?)\s*$")
    for line in m.group(1).splitlines():
        lm = line_re.match(line.strip())
        if not lm:
            continue
        try:
            dt = datetime.strptime(lm.group(1), "%Y-%m-%d").date()
        except Exception:
            continue
        if dt <= today:
            pending.append({
                "date": lm.group(1),
                "tag": lm.group(2).strip("[]").lower(),
                "text": lm.group(3).strip(),
                "raw": line.strip(),
            })
    return pending

def load_config(folder, warnings):
    path = os.path.join(folder, "_pipeline", "config.json")
    try:
        return json.load(open(path))
    except Exception as e:
        warnings.append(f"could not read config.json: {e}")
        return {}


def _read_briefing(folder, name, warnings):
    path = os.path.join(folder, "_pipeline", "briefing", name)
    try:
        return open(path, encoding="utf-8").read()
    except Exception as e:
        warnings.append(f"could not read briefing/{name}: {e}")
        return ""


def _dedup_block(entries, omitted):
    if not entries:
        return "*(No recently covered stories in this category.)*"
    lines = [f"- [{e['date']}] {e['headline']} — {e['core_topic']} (id: {e['id']})"
             for e in entries]
    if omitted:
        lines.append(f"\n*+{omitted} older covered stories from the last 30 days are "
                     "omitted here; a code pass dedups your returns against the full "
                     "window, so avoid rehashing anything already well-covered.*")
    return "\n".join(lines)


def _sources_block(cfg, topic):
    parts = []
    if topic.get("key") == "Wild Card":
        srcs = (cfg.get("wildcard") or {}).get("sources", [])
        parts.append("- " + "\n- ".join(srcs) if srcs else "(see config)")
        return "\n".join(parts)
    official = cfg.get("official_sources", [])
    supplemental = cfg.get("supplemental_sources", [])
    parts.append("Official: " + ", ".join(official))
    parts.append("Supplemental: " + ", ".join(supplemental))
    if topic.get("enrichment") == "state-watch":
        re_sources = cfg.get("real_estate_sources")
        if re_sources:
            parts.append("Topic-specific sources: " + ", ".join(re_sources))
        focus = cfg.get("focus_states", [])
        if focus:
            parts.append("Focus states: " + ", ".join(focus))
    return "\n".join(parts)


def _allowlist_block(cfg, enrichment):
    if enrichment != "regional+divergence":
        return ""
    al = cfg.get("websearch_regional_allowlist", {})
    if not al:
        return ""
    lines = ["- **Regional allowlist (for the regional-perspective search):**"]
    for group, domains in al.items():
        lines.append(f"  - {group}: {', '.join(domains)}")
    return "\n" + "\n".join(lines)


def _focus_markets_block(cfg):
    fm = cfg.get("focus_state_markets", {})
    return "\n".join(f"  - {state}: {'; '.join(markets)}"
                     for state, markets in fm.items())


FIGURE_MODES = ("none", "chart")


def _figures_mode(cfg, topic):
    """Which figure briefing fragment this topic's agent receives.

    Two independent gates, both defaulting to off, so an existing config with no
    `figures` key behaves exactly as it did before the feature existed:
      1. config.figures.enabled -- the whole feature
      2. the topic's own "figures" field

    The wild card is always "none": it is a single additive card whose job is a
    surprising story, not a data readout. An unrecognised mode also falls back to
    "none" rather than raising -- a typo in config should cost a figure, not a run."""
    if not (cfg.get("figures") or {}).get("enabled"):
        return "none"
    if topic.get("_wildcard"):
        return "none"
    mode = topic.get("figures", "none")
    return mode if mode in FIGURE_MODES else "none"


def _focus_state_tags(cfg):
    """The short labels rendered in the State Watch tag, in focus_states order.

    focus_states holds full region names -- they read better in the sources block and
    they key focus_state_markets -- but the rendered tag is a small pill, so it wants
    something short. config.focus_state_tags optionally maps a full name to its tag
    (e.g. "Illinois" -> "IL"); anything unmapped falls back to the full name."""
    tags = cfg.get("focus_state_tags") or {}
    out = []
    for s in cfg.get("focus_states", []):
        if isinstance(s, str) and s.strip():
            out.append(str(tags.get(s, s)))
    return out


def _focus_states_token(cfg):
    """The `state` enum shown in the return schema in briefing/main.md. Built from
    config so a user's own regions appear there -- this used to be a literal list of
    one user's five US states, which every other user's agents were then instructed
    to emit. Falls back to a neutral placeholder when nothing is configured (topics
    without state-watch enrichment are told to leave the field null anyway)."""
    tags = _focus_state_tags(cfg)
    return "|".join(tags) if tags else "region tag"


def _slug(topic):
    return topic.get("slug") or re.sub(r"[^a-z]+", "-", topic.get("key", "topic").lower()).strip("-")


def emit_agent_prompts(folder, today, ctx, warnings, returns_root=None):
    """Stamp per-agent prompt files into state/<date>/prompts/. Returns summary dict."""
    cfg = load_config(folder, warnings)
    state_dir = os.path.join(folder, "_pipeline", "state", today.isoformat())
    prompts_dir = os.path.join(state_dir, "prompts")
    returns_dir = os.path.join(state_dir, "returns")
    os.makedirs(prompts_dir, exist_ok=True)
    os.makedirs(returns_dir, exist_ok=True)
    # Path the AGENTS will use with their Write tool. Defaults to the runtime
    # returns_dir; pass --returns-root if the agents see the folder under a
    # different mount/prefix than this script does.
    agent_returns_root = returns_root or returns_dir

    main_tpl = _read_briefing(folder, "main.md", warnings)
    enrich_tpl = {
        "regional+divergence": _read_briefing(folder, "enrichment_regional.md", warnings),
        "state-watch": _read_briefing(folder, "enrichment_state_watch.md", warnings)
                       .replace("__FOCUS_MARKETS__", _focus_markets_block(cfg)),
        "none": _read_briefing(folder, "enrichment_none.md", warnings),
    }
    # Figure instructions are opt-in twice over: globally via config.figures.enabled and
    # per topic via the topic's "figures" field. figures_none.md is intentionally EMPTY
    # rather than a "do not include figures" sentence -- naming figures in order to
    # forbid them is exactly what makes a model start considering them. An empty splice
    # also keeps a figures-off prompt byte-identical to a pre-feature one.
    figures_cfg = cfg.get("figures") or {}
    figures_on = bool(figures_cfg.get("enabled"))
    figures_tpl = {
        "none": "",
        "chart": _read_briefing(folder, "figures_chart.md", warnings) if figures_on else "",
    }
    wildcard_extra_tpl = _read_briefing(folder, "wildcard_extra.md", warnings)

    blocklist = ", ".join(cfg.get("websearch_blocklist_400s", []))
    feedback_block = ctx["active_feedback"] or "*(No active feedback rules on file.)*"
    budget = cfg.get("agent_search_budget", 4)
    # How many candidates each topic agent is asked to return. The wild card has always
    # had its own count in config; regular topics used a bare literal, so the number
    # could not be tuned without editing this file.
    topic_candidates = int(cfg.get("agent_candidates_per_topic", 3) or 3)

    topics = list(cfg.get("topics", []))
    wc = cfg.get("wildcard") or {}
    wildcard_enabled = bool(wc.get("enabled"))
    if wildcard_enabled:
        topics.append({"key": wc.get("key", "Wild Card"),
                       "slug": wc.get("slug", "wildcard"),
                       "definition": wc.get("definition", ""),
                       "enrichment": "none",
                       "_wildcard": True})

    prompt_paths = {}
    for topic in topics:
        slug = _slug(topic)
        enrichment = topic.get("enrichment", "none")
        figures_block = figures_tpl.get(_figures_mode(cfg, topic), "")
        dedup_entries = ctx["dedup_by_category"].get(topic["key"], [])
        omitted = ctx["dedup_omitted"].get(topic["key"], 0)
        extra = ""
        if topic.get("_wildcard"):
            liked = ctx["liked_history"] or \
                "*(No liked-story history yet — rank purely on consequence + surprise.)*"
            extra = wildcard_extra_tpl.replace("__LIKED_BLOCK__", liked)
        text = (main_tpl
                .replace("__DATE__", today.isoformat())
                .replace("__TOPIC_KEY__", topic["key"])
                .replace("__TOPIC_DEFINITION__", topic.get("definition", ""))
                .replace("__SOURCES_BLOCK__", _sources_block(cfg, topic))
                .replace("__EXTRA_RULES__", extra)
                .replace("__DEDUP_BLOCK__", _dedup_block(dedup_entries, omitted))
                .replace("__FEEDBACK_BLOCK__", feedback_block)
                .replace("__SEARCH_BUDGET__", str(budget))
                .replace("__BLOCKLIST__", blocklist)
                .replace("__ALLOWLIST_BLOCK__", _allowlist_block(cfg, enrichment))
                .replace("__ENRICHMENT_BLOCK__", enrich_tpl.get(enrichment, enrich_tpl["none"]))
                .replace("__FIGURES_BLOCK__", figures_block)
                .replace("__FOCUS_STATES__", _focus_states_token(cfg))
                .replace("__N_CANDIDATES__",
                         str(wc.get("count", topic_candidates) if topic.get("_wildcard")
                             else topic_candidates))
                .replace("__TIMEZONE__", str(cfg.get("digest_timezone", "UTC")))
                .replace("__RETURN_PATH__", os.path.join(agent_returns_root, f"{slug}.json"))
                .replace("__RETURN_FILENAME__", f"{slug}.json")
                .replace("__TOPIC_SLUG__", slug))
        if "__" in text.replace("____", ""):
            strays = sorted(set(re.findall(r"__[A-Z_]{3,}__", text)))
            if strays:
                warnings.append(f"unreplaced placeholders in prompt {slug}: {strays}")
        path = os.path.join(prompts_dir, f"{slug}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        prompt_paths[topic["key"]] = path

    # Full context saved for debugging / retry fallback.
    with open(os.path.join(state_dir, "context.json"), "w", encoding="utf-8") as f:
        json.dump(ctx, f, indent=2, ensure_ascii=False)

    framing_cats = cfg.get("framing_check_categories", ["Middle East"])
    framing = {c: [{"date": e["date"], "headline": e["headline"]}
                   for e in ctx["dedup_by_category"].get(c, [])]
               for c in framing_cats}
    return {
        "today": today.isoformat(),
        "state_dir": state_dir,
        "prompts": prompt_paths,
        "returns_dir": returns_dir,
        "agent_returns_root": agent_returns_root,
        "wildcard_enabled": wildcard_enabled,
        "dedup_counts": ctx["dedup_counts"],
        "dedup_omitted": ctx["dedup_omitted"],
        "active_feedback": ctx["active_feedback"],
        "liked_history": ctx["liked_history"],
        "pending_topics": ctx["pending_topics"],
        "framing_ground_truth": framing,
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=None,
                    help="digest folder; defaults to $DAILY_NEWS_FOLDER or the parent of _pipeline/")
    ap.add_argument("--today", default=None)
    ap.add_argument("--window-days", type=int, default=None,
                    help="history-dedup window in days; defaults to config.json "
                         "`dedup_window_days` (currently 30 if unset)")
    ap.add_argument("--dedup-cap", type=int, default=None,
                    help="max recent dedup entries to inject per category; "
                         "overrides config.json `dedup_inject_cap`. <=0 disables the cap.")
    ap.add_argument("--emit-agent-prompts", action="store_true",
                    help="stamp per-agent prompt files into _pipeline/state/<date>/prompts/ "
                         "and print a compact orchestrator summary instead of the full context")
    ap.add_argument("--returns-root", default=None,
                    help="path prefix agents should use when Writing their return files "
                         "(defaults to the runtime state/<date>/returns dir)")
    args = ap.parse_args()
    folder = resolve_folder(args.folder)
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today \
        else datetime.now().date()
    warnings = []
    window_days = args.window_days if (isinstance(args.window_days, int) and args.window_days > 0) \
        else load_window_days(folder, warnings)
    dedup = build_dedup(folder, today, window_days, warnings)
    full_counts = {k: len(v) for k, v in dedup.items()}

    # Cap the per-category lists injected into agent prompts. Lists are already sorted
    # most-recent-first, so the cap keeps the freshest entries — the ones most likely to
    # be re-surfaced today — and records how many older covered stories were omitted.
    cap = resolve_inject_cap(folder, args.dedup_cap, warnings)

    dedup_omitted = {}
    if isinstance(cap, int) and cap > 0:
        for c in dedup:
            n = len(dedup[c])
            if n > cap:
                dedup[c] = dedup[c][:cap]
                dedup_omitted[c] = n - cap

    result = {
        "today": today.isoformat(),
        "window_days": window_days,
        "dedup_inject_cap": cap,
        "dedup_by_category": dedup,
        "dedup_counts": full_counts,
        "dedup_omitted": dedup_omitted,
        "active_feedback": build_feedback(folder, warnings),
        "liked_history": build_liked_history(folder, warnings),
        "pending_topics": build_pending_topics(folder, today, warnings),
        "warnings": warnings,
    }
    if args.emit_agent_prompts:
        summary = emit_agent_prompts(folder, today, result, warnings,
                                     returns_root=args.returns_root)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
