#!/usr/bin/env python3
"""
Phase 2 helper: deterministic intake + triage of the parallel topic agents' returns.

Does the mechanical work so the orchestrator model doesn't have to reason over 15
raw candidates and their URLs:

  * parse the 5 agent JSON returns (file with an array, {"returns":[...]}, a dir of
    per-agent .json files, or stdin)
  * validate each return + candidate against the schema; drop malformed ones (reported,
    never crash)
  * URL / source pre-screen: flag landing-page URLs (no article slug / bare domain) and
    known-aggregator domains; count distinct named outlets per candidate
  * resolve an effective source_quality (uses the agent's value if given, else infers)
  * apply the sourcing PROMOTION GUARD structurally: any candidate suggested A/B that
    isn't carried by >=2 named outlets or a primary source is capped at Tier C
  * first-pass cross-agent dedup: flag candidate pairs whose core_topic is similar, for
    the model to adjudicate (does NOT auto-merge)
  * history dedup: precise fuzzy match of every surviving candidate against the FULL
    30-day story-history (the injected agent lists are capped to save tokens, so this
    code-side pass — not the model eyeballing a truncated list — is what makes dedup
    reliable). Flags candidates that restate an already-covered story so the model can
    drop them or mark them an explicit update.

Output: one compact triage JSON to stdout. The model then makes the final semantic
calls on the shortlist instead of eyeballing everything.

Usage:
  intake_candidates.py --in returns.json        # array or {"returns":[...]}
  intake_candidates.py --in-dir agent_returns/  # one .json per agent
  cat returns.json | intake_candidates.py
  # optional: --folder PATH (else dynamic), --config PATH,
  #           --today YYYY-MM-DD, --window-days N (history dedup window)
"""
import argparse, difflib, glob, json, os, re, sys
from datetime import datetime, timedelta
from urllib.parse import urlparse

_LEGACY_DEFAULT_FOLDER = os.path.expanduser("~/Daily news")  # portable last-resort only; real resolution is --folder > $DAILY_NEWS_FOLDER > parent of _pipeline/

# Valid topic keys are loaded from config.topics at run time (see load_valid_topics); this
# lets intake accept whatever topic set the user configured instead of a hardcoded list.
VALID_TIERS = {"A", "B", "C"}
VALID_STATUS = {"ok", "no_qualifying_story", "error"}
REQUIRED_CAND_FIELDS = ("headline", "core_topic", "suggested_tier", "key_facts",
                        "sources", "urls")

# Fallback screen config if config.json lacks a source_screen block.
DEFAULT_SCREEN = {
    "aggregator_denylist": ["crescendo.ai", "capital.com", "indmoney.com",
                            "thetechmarketer.com", "moneywise.com"],
    "primary_domain_hints": [".gov", "arxiv.org", "sec.gov", ".edu", "doi.org",
                             "nature.com", "science.org", "prnewswire.com",
                             "businesswire.com"],
    "dedup_similarity_threshold": 0.6,
    # Event-key / entity check (catches "same underlying report, different framing"):
    "entity_similarity_threshold": 0.6,
    "min_shared_entities": 3,
    "min_entities_for_check": 4,
}

# Generic section words that, alone, signal a landing/section page (not an article).
SECTION_WORDS = {"news", "sectors", "technology", "tech", "space", "space_time",
                 "markets", "world", "business", "science", "articles", "topics",
                 "category", "section", "latest", "home"}


def resolve_folder(arg=None):
    if arg:
        return arg
    if os.environ.get("DAILY_NEWS_FOLDER"):
        return os.environ["DAILY_NEWS_FOLDER"]
    try:
        return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    except NameError:
        return _LEGACY_DEFAULT_FOLDER


def load_screen(folder, config_path):
    path = config_path or os.path.join(folder, "_pipeline", "config.json")
    try:
        cfg = json.load(open(path))
        ss = cfg.get("source_screen") or {}
    except Exception:
        ss = {}
    out = dict(DEFAULT_SCREEN)
    out.update({k: v for k, v in ss.items() if not k.startswith("_")})
    return out


def domain(url):
    try:
        net = urlparse(url).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def looks_like_slug(seg):
    # A real article slug is hyphenated, OR carries a date/id run of digits, OR is a
    # very long single token. Underscored section names (space_time, space_exploration)
    # and short generic words do NOT count.
    if seg.lower() in SECTION_WORDS:
        return False
    if "-" in seg:
        return True
    if re.search(r"\d{4,}", seg):
        return True
    return len(seg) >= 20


def classify_url(url, screen):
    """Return (verdict, reason): verdict in {ok, landing, aggregator, shallow}."""
    if not isinstance(url, str) or not url.strip():
        return "shallow", "empty url"
    dom = domain(url)
    if not dom:
        return "shallow", "unparseable url"
    for bad in screen["aggregator_denylist"]:
        if dom == bad or dom.endswith("." + bad):
            return "aggregator", f"aggregator domain ({dom})"
    path = urlparse(url).path.strip("/")
    segs = [s for s in path.split("/") if s]
    if not segs:
        return "landing", f"bare domain root ({dom})"
    if any(looks_like_slug(s) for s in segs):
        return "ok", ""
    # No slug anywhere: shallow section page if all segments are generic words.
    if all(s.lower() in SECTION_WORDS for s in segs):
        return "landing", f"section/landing page (/{path})"
    if len(segs) < 2:
        return "shallow", f"shallow path, no article slug (/{path})"
    return "shallow", f"no recognizable article slug (/{path})"


def distinct_outlets(sources):
    if not isinstance(sources, list):
        return 0
    seen = set()
    for s in sources:
        if isinstance(s, str) and s.strip():
            seen.add(re.sub(r"\s+", " ", s.strip().lower()))
    return len(seen)


def is_primary_domain(url, screen):
    dom = domain(url)
    return any(h in dom or h in (url or "") for h in screen["primary_domain_hints"])


def is_primary_article(url, screen):
    # Counts as a primary source only if it is a DEEP article on a primary domain —
    # a bare nasa.gov/ root or a section page does not qualify.
    return is_primary_domain(url, screen) and classify_url(url, screen)[0] == "ok"


def resolve_quality(cand, url_verdicts, n_outlets, has_primary, screen):
    """Trust the agent's source_quality if it gave a valid one; else infer."""
    given = (cand.get("source_quality") or "").strip().lower()
    if given in {"primary", "major_outlet", "secondary", "weak"}:
        return given, "agent-provided"
    # Inference.
    good_urls = [v for v in url_verdicts if v == "ok"]
    bad_urls = [v for v in url_verdicts if v in ("aggregator", "landing", "shallow")]
    if has_primary:
        return "primary", "inferred: primary_source_url / primary domain present"
    if not good_urls and bad_urls:
        return "weak", "inferred: only aggregator/landing URLs"
    if n_outlets >= 2 and good_urls:
        return "major_outlet", "inferred: >=2 outlets + >=1 deep article URL"
    return "secondary", "inferred: default"


def screen_candidate(cand, screen):
    urls = cand.get("urls") if isinstance(cand.get("urls"), list) else []
    verdicts, url_flags = [], []
    for u in urls:
        v, reason = classify_url(u, screen)
        verdicts.append(v)
        if v != "ok":
            url_flags.append({"url": u, "issue": v, "reason": reason})
    n_outlets = distinct_outlets(cand.get("sources"))
    psu = cand.get("primary_source_url")
    has_primary = (bool(psu and isinstance(psu, str) and psu.strip())
                   and is_primary_article(psu, screen)) or \
        any(is_primary_article(u, screen) for u in urls)
    quality, q_basis = resolve_quality(cand, verdicts, n_outlets, has_primary, screen)

    # Promotion guard: A/B requires (primary OR >=2 outlets) AND >=1 deep URL AND
    # a non-weak resolved quality.
    has_deep = any(v == "ok" for v in verdicts)
    passes_ab = (quality in ("primary", "major_outlet") and
                 (has_primary or n_outlets >= 2) and has_deep)
    suggested = (cand.get("suggested_tier") or "").strip().upper()
    reasons = []
    if not passes_ab:
        if quality in ("secondary", "weak"):
            reasons.append(f"source_quality={quality}")
        if not (has_primary or n_outlets >= 2):
            reasons.append(f"only {n_outlets} distinct outlet(s), no primary source")
        if not has_deep:
            reasons.append("no deep-link article URL")
    demote_to = "C" if (suggested in ("A", "B") and not passes_ab) else None

    return {
        "distinct_outlets": n_outlets,
        "has_primary_source": has_primary,
        "resolved_source_quality": quality,
        "quality_basis": q_basis,
        "url_flags": url_flags,
        "n_urls": len(urls),
        "passes_AB": passes_ab,
        "suggested_tier": suggested or None,
        "effective_tier_cap": demote_to or suggested or None,
        "demote_to": demote_to,
        "demote_reasons": reasons,
    }


def normalize_text(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def similar(a, b):
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def _content_tokens(s):
    return {t for t in normalize_text(s).split() if len(t) > 2}

# Containment only fires when the shorter side has at least this many content tokens,
# so a trivially short candidate can't spuriously "contain" into a long history entry.
_MIN_TOKENS_FOR_CONTAINMENT = 4


def token_overlap(a, b):
    """Jaccard overlap of content tokens — a symmetric second signal that catches texts
    sharing the same key entities when order/length drags SequenceMatcher down."""
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def token_containment(a, b):
    """Directional overlap coefficient: |A∩B| / min(|A|,|B|). This is the signal that
    actually matters for history dedup — the history `core_topic`s are long and detailed
    (60+ tokens), so a candidate that restates an already-covered story shares almost all
    of *its* tokens with the history entry even though Jaccard/difflib stay low. Asks: is
    the shorter text largely contained in the longer one?"""
    ta, tb = _content_tokens(a), _content_tokens(b)
    if min(len(ta), len(tb)) < _MIN_TOKENS_FOR_CONTAINMENT:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# ---------------------------------------------------------------------------
# Event-key / entity signature — catches "same underlying report, different
# framing." Text similarity (difflib/Jaccard/containment) collapses when two
# desks reframe the SAME underlying report: different headline, different lede,
# different angle. But the *event key* survives reframing — the named
# org/person/place, the ticker/acronym, the salient number (dollar figure,
# percent, version, magnitude, count), and the cited source URL. We extract that
# signature per candidate and flag pairs / history entries that share it even
# when prose similarity stays below threshold, so the model can adjudicate.
# ---------------------------------------------------------------------------

# Runs of Capitalized words = proper-noun phrases (people, orgs, places, products).
_PROPER_RE = re.compile(r"[A-Z][A-Za-z0-9.&'’-]*(?:\s+[A-Z][A-Za-z0-9.&'’-]*){0,3}")
# All-caps tokens = acronyms / tickers (NASA, FDA, IDF, GDP, SPY).
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")
# Salient numbers: money, percentages, version/decimals, and long integers.
_NUM_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:trillion|billion|million|bn|mn|b|m|k)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:percent|%)"
    r"|\b\d+\.\d+\b"
    r"|\b\d{3,}\b",
    re.IGNORECASE,
)

# Words Capitalized only because they lead a sentence / are generic — not real
# entities. Kept short; the min-shared-entities gate does the rest.
_ENTITY_STOPWORDS = {
    "the", "a", "an", "this", "that", "these", "those", "it", "its", "new",
    "update", "report", "reports", "today", "after", "amid", "as", "how", "why",
    "what", "when", "where", "first", "for", "and", "but", "with", "from",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}


def _norm_entity(s):
    return re.sub(r"\s+", " ", s.strip().strip(".,;:'\"’").lower())


def _norm_number(s):
    # Collapse "$4.8 billion" / "4,800" spacing so equal magnitudes match.
    return re.sub(r"\s+", "", s.strip().lower()).replace(",", "")


def entity_signature(*texts):
    """Set of distinctive event-key tokens from the given text fields: proper-noun
    phrases, acronyms/tickers, and salient numbers. Case-preserved input matters —
    capitals mark the proper nouns."""
    sig = set()
    for t in texts:
        if isinstance(t, (list, tuple)):
            t = " ".join(str(x) for x in t)
        t = str(t or "")
        for m in _PROPER_RE.findall(t):
            words = [w for w in _norm_entity(m).split() if w not in _ENTITY_STOPWORDS]
            if words:
                sig.add(" ".join(words))
        for m in _ACRONYM_RE.findall(t):
            e = _norm_entity(m)
            if e and e not in _ENTITY_STOPWORDS:
                sig.add(e)
        for m in _NUM_RE.findall(t):
            n = _norm_number(m)
            if n and n not in {"", "0"}:
                sig.add(n)
    return sig


def entity_key_score(sig_a, sig_b, min_size):
    """Directional containment over event-key signatures: |A∩B| / min(|A|,|B|).
    Returns (score, sorted_shared). Guarded by min_size so a tiny signature can't
    spuriously 'contain' into a larger one."""
    if min(len(sig_a), len(sig_b)) < min_size:
        return 0.0, []
    shared = sig_a & sig_b
    if not shared:
        return 0.0, []
    return len(shared) / min(len(sig_a), len(sig_b)), sorted(shared)


def url_key(url):
    """Normalize a URL to domain + path (no scheme/query/fragment) so the same
    article cited by two candidates collapses to one key. Bare-domain / homepage
    URLs return '' — too generic to be an event key."""
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


def candidate_url_keys(cand):
    """Article-level URL keys from a raw agent candidate (primary_source_url + urls)."""
    urls = cand.get("urls") if isinstance(cand.get("urls"), list) else []
    keys = set()
    for u in [cand.get("primary_source_url")] + urls:
        k = url_key(u)
        if k:
            keys.add(k)
    return keys


# LEGACY history normalization only — safe to ignore for new installs. Early
# story-history.json data used messy label variants ("AI" vs "AI & Tech"); this map
# collapses those known variants so old entries dedup correctly. Categories not listed
# here pass through UNCHANGED (see canon_category), so any topic set a user configures
# works without touching this map. Valid topics come from config.topics at run time.
HISTORY_CATEGORY_ALIASES = {
    "ai": "AI & Tech", "ai & tech": "AI & Tech", "ai and tech": "AI & Tech",
    "tech": "AI & Tech", "middle east": "Middle East", "space": "Space",
    "quantum": "Quantum", "real estate": "Real Estate", "on your radar": "On Your Radar",
}


def canon_category(raw):
    if not raw:
        return "Uncategorized"
    return HISTORY_CATEGORY_ALIASES.get(str(raw).strip().lower(), str(raw).strip())


def load_window_days(folder, config_path, fallback=30):
    path = config_path or os.path.join(folder, "_pipeline", "config.json")
    try:
        val = json.load(open(path)).get("dedup_window_days")
        if isinstance(val, int) and val > 0:
            return val
    except Exception:
        pass
    return fallback


def load_history(folder, today, window_days):
    """Load story-history entries within [today-window_days, today]. Never crashes —
    returns ([], warning) on any read/parse failure."""
    path = os.path.join(folder, "story-history.json")
    try:
        stories = json.load(open(path)).get("stories", [])
    except Exception as e:
        return [], f"could not read story-history.json for history dedup: {e}"
    cutoff = today - timedelta(days=window_days)
    out = []
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
        out.append({
            "id": s.get("id") or s.get("story_id"),
            "headline": s.get("headline", ""),
            "core_topic": s.get("core_topic", ""),
            "category": canon_category(s.get("category")),
            "date": d,
        })
    return out, None


def _field_score(a, b):
    """Best of three signals for one field pair: difflib ratio, Jaccard, containment."""
    return max(similar(a, b), token_overlap(a, b), token_containment(a, b))


def match_against_history(cand, cand_sig, history, thr, ent_thr, min_shared, min_size):
    """Best fuzzy match of a candidate against the in-window history. For each history
    entry: (1) text — score core_topic and headline with the max of difflib ratio,
    Jaccard, and directional containment; (2) event-key — containment over the entity
    signatures, which catches an already-covered story restated with different framing.
    A history entry matches if EITHER path clears its threshold; the strongest match is
    kept. Returns a match dict, or None if nothing clears."""
    best = None
    for h in history:
        s_core = _field_score(cand.get("core_topic"), h["core_topic"])
        s_head = _field_score(cand.get("headline"), h["headline"])
        text_score = max(s_core, s_head)
        ent_score, shared_ents = entity_key_score(cand_sig, h.get("_sig", set()), min_size)
        entity_hit = ent_score >= ent_thr and len(shared_ents) >= min_shared

        if text_score >= thr:
            matched_field = "core_topic" if s_core >= s_head else "headline"
            strength = max(text_score, ent_score)
        elif entity_hit:
            matched_field = "entity"
            strength = ent_score
        else:
            continue

        if best is None or strength > best["similarity"]:
            best = {
                "history_id": h["id"],
                "history_date": h["date"],
                "history_category": h["category"],
                "history_headline": h["headline"],
                "history_core_topic": h["core_topic"],
                "similarity": round(strength, 3),
                "matched_field": matched_field,
                "matched_by": "entity" if matched_field == "entity" else "text",
                "text_similarity": round(text_score, 3),
                "entity_score": round(ent_score, 3),
                "shared_entities": shared_ents[:8],
                "same_category": canon_category(cand.get("topic")) == h["category"],
            }
    return best


def load_returns(args):
    blobs = []
    if args.in_dir:
        for fp in sorted(glob.glob(os.path.join(args.in_dir, "*.json"))):
            blobs.append(json.load(open(fp)))
    else:
        raw = open(args.infile).read() if args.infile else sys.stdin.read()
        data = json.loads(raw)
        if isinstance(data, dict) and "returns" in data:
            data = data["returns"]
        if isinstance(data, dict):           # a single agent return
            data = [data]
        blobs = data
    return blobs


def load_valid_topics(folder, config_path=None):
    """Set of valid topic keys from config.topics + the Wild Card key. Returns None if
    config can't be read, in which case intake is permissive (accepts any topic key)."""
    path = config_path or os.path.join(folder, "_pipeline", "config.json")
    try:
        cfg = json.load(open(path))
    except Exception:
        return None
    valid = {t["key"] for t in cfg.get("topics", []) if t.get("key")}
    wc = cfg.get("wildcard") or {}
    if wc.get("key"):
        valid.add(wc["key"])
    return valid or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default=None,
                    help="JSON file: array of agent returns or {\"returns\":[...]}; omit to read stdin")
    ap.add_argument("--in-dir", dest="in_dir", default=None,
                    help="directory of per-agent .json files")
    ap.add_argument("--folder", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--today", default=None,
                    help="YYYY-MM-DD for the history-dedup window; defaults to system date")
    ap.add_argument("--window-days", type=int, default=None,
                    help="history-dedup window; defaults to config `dedup_window_days` or 30")
    ap.add_argument("--out", default=None,
                    help="also write the triage JSON to this path (checkpoint), e.g. "
                         "_pipeline/state/<date>/triage.json")
    ap.add_argument("--log-md", dest="log_md", default=None,
                    help="write the mechanical run-log sections (agent table, intake "
                         "counts, dedup/history flags, drops) as markdown to this path")
    args = ap.parse_args()

    folder = resolve_folder(args.folder)
    screen = load_screen(folder, args.config)
    valid_topics = load_valid_topics(folder, args.config)
    try:
        today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today \
            else datetime.now().date()
    except Exception:
        today = datetime.now().date()
    window_days = args.window_days if (isinstance(args.window_days, int) and args.window_days > 0) \
        else load_window_days(folder, args.config)

    try:
        returns = load_returns(args)
    except Exception as e:
        print(json.dumps({"error": f"could not parse input: {e}"}))
        sys.exit(2)

    topics_report, candidates, dropped = {}, [], []
    # Per-candidate event keys (entity signature + article-level URL keys), keyed by
    # candidate id. Kept out of the serialized triage to avoid bloat; used by the
    # cross-agent and history dedup passes to catch same-report/different-framing.
    event_keys = {}

    for ret in returns:
        if not isinstance(ret, dict):
            dropped.append({"topic": None, "reason": "return is not an object"})
            continue
        topic = ret.get("topic")
        status = ret.get("status")
        known = (valid_topics is None) or (topic in valid_topics)
        tkey = topic if known else f"<invalid:{topic!r}>"
        rep = topics_report.setdefault(tkey, {"status": status, "n_candidates": 0,
                                              "n_valid": 0, "n_dropped": 0})
        if not known:
            rep["note"] = "unknown topic key"
        if status not in VALID_STATUS:
            rep["status_note"] = f"invalid status {status!r}"
        cands = ret.get("candidates") if isinstance(ret.get("candidates"), list) else []
        rep["n_candidates"] += len(cands)

        slug = re.sub(r"[^a-z]+", "-", (topic or "topic").lower()).strip("-")
        for i, cand in enumerate(cands):
            missing = [f for f in REQUIRED_CAND_FIELDS
                       if f not in cand or cand.get(f) in (None, "", [])]
            if not isinstance(cand, dict) or missing:
                rep["n_dropped"] += 1
                dropped.append({"topic": topic, "index": i,
                                "reason": f"missing/empty required fields: {missing}"})
                continue
            tier = (cand.get("suggested_tier") or "").strip().upper()
            if tier not in VALID_TIERS:
                rep["n_dropped"] += 1
                dropped.append({"topic": topic, "index": i,
                                "reason": f"invalid suggested_tier {cand.get('suggested_tier')!r}"})
                continue
            rep["n_valid"] += 1
            scr = screen_candidate(cand, screen)
            cand_id = f"{slug}-c{i}"
            event_keys[cand_id] = {
                "entities": entity_signature(cand.get("headline"),
                                             cand.get("core_topic"),
                                             cand.get("key_facts")),
                "urls": candidate_url_keys(cand),
            }
            # Triage is deliberately BODILESS: it carries every short field the
            # orchestrator needs to SELECT (tier verdicts, confidence, key_facts,
            # hook) but never the body/enrichment prose — assemble_stories.py
            # merges those from the return files for the picked IDs only, so full
            # story text never has to pass through the orchestrator's context.
            candidates.append({
                "id": cand_id,
                "topic": topic,
                "headline": cand.get("headline"),
                "core_topic": cand.get("core_topic"),
                "toc_hook": cand.get("toc_hook"),
                "key_facts": cand.get("key_facts"),
                "confidence": cand.get("confidence"),
                "is_update_of": cand.get("is_update_of"),
                "update_badge": cand.get("update_badge"),
                "primary_source_url": cand.get("primary_source_url"),
                "sources": cand.get("sources"),
                "has_body": bool(cand.get("body")),
                "has_regional": bool(cand.get("regional")),
                "has_divergence": bool(cand.get("divergence")),
                "has_state_watch": bool(cand.get("state_watch")),
                **scr,
            })

    # First-pass cross-agent dedup. Two signals, either of which flags a pair for the
    # model to adjudicate (never auto-merged):
    #   1. TEXT — difflib similarity on core_topic / headline (the original signal).
    #   2. EVENT KEY — a shared article URL, or high containment over the entity
    #      signatures. This is what catches "same underlying report, different framing":
    #      two agents surface the same report with different wording, so the text score
    #      stays low, but they cite the same source and/or share the same named
    #      entities and salient numbers.
    thr = float(screen.get("dedup_similarity_threshold", 0.6))
    ent_thr = float(screen.get("entity_similarity_threshold", 0.6))
    min_shared = int(screen.get("min_shared_entities", 3))
    min_size = int(screen.get("min_entities_for_check", 4))
    dedup_pairs = []
    for a in range(len(candidates)):
        for b in range(a + 1, len(candidates)):
            ca, cb = candidates[a], candidates[b]
            s_core = similar(ca["core_topic"], cb["core_topic"])
            s_head = similar(ca["headline"], cb["headline"])
            text_score = max(s_core, s_head)
            eka, ekb = event_keys.get(ca["id"], {}), event_keys.get(cb["id"], {})
            shared_urls = sorted(eka.get("urls", set()) & ekb.get("urls", set()))
            ent_score, shared_ents = entity_key_score(
                eka.get("entities", set()), ekb.get("entities", set()), min_size)
            entity_hit = ent_score >= ent_thr and len(shared_ents) >= min_shared

            signals = []
            if text_score >= thr:
                signals.append("text")
            if shared_urls:
                signals.append("shared_url")
            if entity_hit:
                signals.append("entity")
            if not signals:
                continue
            # A shared source URL is the strongest same-report evidence.
            strength = max(text_score, ent_score, 1.0 if shared_urls else 0.0)
            dedup_pairs.append({
                "a": ca["id"], "b": cb["id"],
                "a_topic": ca["topic"], "b_topic": cb["topic"],
                "similarity": round(text_score, 3),
                "entity_score": round(ent_score, 3),
                "signals": signals,
                "shared_entities": shared_ents[:8],
                "shared_source_urls": shared_urls[:4],
                "_strength": round(strength, 3),
                "a_core": ca["core_topic"], "b_core": cb["core_topic"],
            })
    dedup_pairs.sort(key=lambda p: p.pop("_strength"), reverse=True)

    # History dedup: match every surviving candidate against the FULL in-window history
    # (the injected agent lists were capped, so this code-side pass is what makes dedup
    # reliable). Annotate each candidate in-place and collect a flat report.
    history, hist_warning = load_history(folder, today, window_days)
    for h in history:
        h["_sig"] = entity_signature(h["headline"], h["core_topic"])
    history_matches = []
    for c in candidates:
        cand_for_match = {"core_topic": c["core_topic"], "headline": c["headline"],
                          "topic": c["topic"]}
        cand_sig = event_keys.get(c["id"], {}).get("entities", set())
        m = match_against_history(cand_for_match, cand_sig, history, thr,
                                  ent_thr, min_shared, min_size)
        c["history_match"] = m
        if m:
            history_matches.append({"candidate_id": c["id"], "topic": c["topic"], **m})
    history_matches.sort(key=lambda p: p["similarity"], reverse=True)

    n_demoted = sum(1 for c in candidates if c["demote_to"])
    result = {
        "topics": topics_report,
        "counts": {
            "returns": len(returns),
            "valid_candidates": len(candidates),
            "dropped_candidates": len(dropped),
            "flagged_for_demotion": n_demoted,
            "dedup_pairs_flagged": len(dedup_pairs),
            "history_dupes_flagged": len(history_matches),
        },
        "candidates": candidates,
        "dropped": dropped,
        "dedup_pairs": dedup_pairs,
        "history_matches": history_matches,
        "history_dedup": {
            "today": today.isoformat(),
            "window_days": window_days,
            "history_entries_checked": len(history),
            "warning": hist_warning,
        },
        "screen_config": {
            "aggregator_denylist": screen["aggregator_denylist"],
            "dedup_similarity_threshold": thr,
            "entity_similarity_threshold": ent_thr,
            "min_shared_entities": min_shared,
            "min_entities_for_check": min_size,
        },
    }
    out_json = json.dumps(result, indent=2, ensure_ascii=False)
    print(out_json)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(out_json)
        os.replace(tmp, args.out)
    if args.log_md:
        os.makedirs(os.path.dirname(args.log_md) or ".", exist_ok=True)
        with open(args.log_md, "w", encoding="utf-8") as f:
            f.write(render_log_md(result, today))


def render_log_md(result, today):
    """Mechanical run-log sections — everything the orchestrator used to hand-type.
    The orchestrator appends only the editorial 'Selection Decisions' section."""
    lines = [f"## Agent Results ({today.isoformat()})", "",
             "| Topic | Status | Candidates | Valid | Dropped |",
             "|-------|--------|-----------|-------|---------|"]
    for topic, rep in sorted(result["topics"].items()):
        lines.append(f"| {topic} | {rep.get('status')} | {rep.get('n_candidates', 0)} "
                     f"| {rep.get('n_valid', 0)} | {rep.get('n_dropped', 0)} |")
    c = result["counts"]
    lines += ["", "## Intake Summary", "",
              f"- {c['valid_candidates']} valid candidates, "
              f"{c['dropped_candidates']} dropped",
              f"- {c['flagged_for_demotion']} flagged for demotion "
              "(single-outlet or secondary sourcing)",
              f"- {c['dedup_pairs_flagged']} cross-agent dedup pair(s) flagged",
              f"- {c['history_dupes_flagged']} history match(es) flagged",
              f"- History entries checked: "
              f"{result['history_dedup']['history_entries_checked']} "
              f"({result['history_dedup']['window_days']}-day window)"]
    if result["dedup_pairs"]:
        lines += ["", "### Dedup pairs", ""]
        for p in result["dedup_pairs"]:
            sig = ",".join(p.get("signals", [])) or "text"
            extra = ""
            if p.get("shared_source_urls"):
                extra += f", shared URL: {p['shared_source_urls'][0]}"
            elif "entity" in p.get("signals", []) and p.get("shared_entities"):
                extra += (f", entity {p.get('entity_score')} on "
                          f"[{', '.join(p['shared_entities'][:4])}]")
            lines.append(f"- {p['a']} vs {p['b']} — text similarity "
                         f"{p['similarity']} [{sig}]{extra}")
    if result["history_matches"]:
        lines += ["", "### History matches", ""]
        for m in result["history_matches"]:
            extra = ""
            if m.get("matched_by") == "entity" and m.get("shared_entities"):
                extra = (f", entity {m.get('entity_score')} on "
                         f"[{', '.join(m['shared_entities'][:4])}]")
            lines.append(f"- {m['candidate_id']} → {m['history_id']} "
                         f"[{m['history_date']}] similarity {m['similarity']} "
                         f"({m['matched_field']}, same_category={m['same_category']})"
                         f"{extra}")
    if result["dropped"]:
        lines += ["", "### Dropped at intake", ""]
        for d in result["dropped"]:
            lines.append(f"- {d.get('topic')}[{d.get('index', '?')}]: {d['reason']}")
    demoted = [x for x in result["candidates"] if x.get("demote_to")]
    if demoted:
        lines += ["", "### Demotions (promotion guard)", ""]
        for x in demoted:
            lines.append(f"- {x['id']} ({x['topic']}): {'; '.join(x['demote_reasons'])}")
    if result["history_dedup"].get("warning"):
        lines += ["", f"**History-dedup warning:** {result['history_dedup']['warning']}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
