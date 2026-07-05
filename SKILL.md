---
name: daily-news-digest
description: Daily configurable-count story digest across your configured topics, plus an optional additive Wild Card steered by 👍/👎 history. File-based agent pipeline: emitted prompt files → parallel topic agents write return files → bodiless triage → assemble/build/validate, with phase checkpoints in _pipeline/state/YYYY-MM-DD/ for resumable retries. Dup-output guard + concurrent-run lock.
---

You are the **orchestrator** for the user's daily news digest. Produce one HTML file in
the **project folder** (referred to as `$PROJECT` below), named
`YYYY-MM-DD-news-digest.html` for today's date.

**Resolving `$PROJECT` (portable — no hardcoded home paths).** `$PROJECT` is the folder
that contains this skill and its `_pipeline/` directory. Resolve it once at the start of
the run, then substitute the real absolute path for `$PROJECT` in every command below (or
prefix each command with `PROJECT="/abs/path" &&` — shell calls don't share environment).
The `_pipeline/*.py` scripts also self-resolve the folder in this order: `--folder`
argument → `$DAILY_NEWS_FOLDER` env var → the parent of the script's own directory. So as
long as you invoke a script by its correct path inside `$PROJECT/_pipeline/`, it finds the
project folder on its own.

Architecture (file-based, token-lean, resumable): deterministic scripts stamp out
self-contained **prompt files** for the parallel agents (one per configured topic, plus
one wild-card agent if `config.wildcard.enabled`); agents **write their returns to
files** and reply one line; intake produces a **bodiless triage** you select from;
`assemble_stories.py` merges the picked candidates' full text into the stories JSON
**so story bodies never pass through your context**; `build_digest.py` renders;
validation gates; state writes back atomically. Every phase checkpoints into
`_pipeline/state/YYYY-MM-DD/` so a retry run can **resume** instead of re-running.
Guiding rules for an unattended early-morning run:

1. **Never abort the whole digest because one piece failed** — degrade gracefully, log it.
2. **Emit content, not boilerplate.** Never hand-write card/TOC/CSS markup, never read an
   old digest as an HTML reference, never re-type text a script can move for you. The
   long-form gathering rules live in `_pipeline/briefing/*.md` (that is where to edit
   them), not in this prompt.

Paths (all relative to `$PROJECT`):
- Digest folder + state files: `$PROJECT/`
- Pipeline: `$PROJECT/_pipeline/` — `config.json` is the single source of truth for
  topics, sources, counts, budgets, and colors; prefer it over any value restated here.
- Run state: `$PROJECT/_pipeline/state/YYYY-MM-DD/` (created by Phase 0)

---

## PRE-PHASE 0 — Run guards (in order, before anything else)

### 0a — Duplicate-output guard
```bash
python3 "$PROJECT/_pipeline/check_existing_digest.py" \
  --today $(date +%F) --window-minutes 90
```
- **Exit 100 → ABORT.** A valid digest already exists (manual run too close to schedule).
  Call `mcp__cowork__present_files` with the `EXISTING_DIGEST_PATH` it prints and stop.
  No lock taken, nothing to release, no run log needed.
- **Exit 0 → proceed** to 0b.

### 0b — Concurrent-run lock
```bash
python3 "$PROJECT/_pipeline/run_lock.py" acquire
```
- **Exit 101 → ABORT.** Another run is in progress. Stop immediately; do **not** call
  `release` (you never acquired it).
- **Exit 0 → proceed.** You hold the lock. From here, **every** exit path — success or
  abort — must end with `run_lock.py release` (Phase 4 step 6). The lock self-expires
  after 30 min, and while you work your checkpoint writes double as a liveness beacon
  for any safety-net retry run.

### 0c — Resume check (makes retries cheap; costs one command on a normal run)
```bash
python3 "$PROJECT/_pipeline/checkpoint.py" status --today $(date +%F)
```
- If `failed` is non-null: a previous attempt today hit a systematic (non-transient)
  failure. Do not blindly re-run into the same wall — read the FAILED reason; only
  proceed if you can address it, otherwise log and stop (releasing the lock).
- If `phase` is not `none`, an earlier attempt today already completed work: **resume**
  from the furthest checkpoint instead of redoing it — `context` → skip to Phase 1
  (prompts are already emitted); `agents` → run Phase 1 only for `agents_missing`, then
  continue; `triage` → skip to Phase 2 selection; `stories` → skip to Phase 3 build.
  Note the resume point in the run log.
- If `phase` is `none`, run everything.

---

## PHASE 0 — Deterministic setup (one command)

```bash
python3 "$PROJECT/_pipeline/build_context.py" \
  --today $(date +%F) --emit-agent-prompts
```
This stamps a self-contained prompt file per agent into the day's `state/.../prompts/`
(briefing templates + topic definition + per-category dedup slice + 👎 feedback rules;
the wild-card prompt also gets the 👍 liked history), creates the day's
`state/.../returns/`, saves `context.json`, and prints a **compact summary** — the only
context you hold: `prompts` (paths), `returns_dir`, `wildcard_enabled`,
`dedup_counts`/`dedup_omitted`, `active_feedback` (👎 rules), `liked_history` (👍
patterns), `pending_topics`, and `framing_ground_truth` (recent headlines for
ongoing-conflict categories — keep these, Phase 2 needs them verbatim). Do **not** read
`context.json` or the prompt files back into your context; they are for the agents.

If the script errors: fall back to running it without `--emit-agent-prompts` and
building agent prompts by hand from the full context JSON (the old flow), and note the
fallback in the run log.

---

## PHASE 1 — Parallel gathering (one agent per prompt file, file-based returns)

Launch **all agents in parallel** (single message, one Agent call per prompt file in the
Phase 0 summary; skip `wildcard` if `wildcard_enabled` is false). Each agent's prompt is
only:

> You are a news-gathering agent. Read the file [absolute path to that agent's prompt
> file, from the Phase 0 summary] and follow it exactly: research the beat it defines,
> then WRITE your return JSON to the exact path it names, and reply with a single
> status line only.

Everything else — sources, dedup, feedback, search budget, blocklist, tiers, promotion
guard, claim-level sourcing, enrichment rules, return schema — is in the prompt file.

**Blip recovery (one bounded retry, in-run).** Soft deadline = `agent_deadline_seconds`
in config. When the wave settles, check the day's `returns/` dir (or `checkpoint.py
status` → `agents_missing`). For each agent that errored, missed the deadline, or wrote
malformed/unparseable JSON, re-launch it **once** with the same prompt file plus one
line: "Reduced budget: at most `agent_retry.reduced_search_budget` searches, return
`agent_retry.reduced_candidates` candidates." A transient 529/429/timeout usually clears
immediately; if the retry also fails, **drop that topic and continue** — no further
retries, no abort. Record per-agent outcomes (first try / retry / dropped) for the run log.

---

## PHASE 2 — Deterministic intake, then selection

```bash
python3 "$PROJECT/_pipeline/intake_candidates.py" \
  --in-dir "$PROJECT/_pipeline/state/$(date +%F)/returns" \
  --today $(date +%F) \
  --out "$PROJECT/_pipeline/state/$(date +%F)/triage.json" \
  --log-md "$PROJECT/_pipeline/state/$(date +%F)/intake-log.md"
```
The triage is **bodiless by design**: per candidate you get headline, core_topic,
toc_hook, key_facts, confidence, sources, tier verdicts (`passes_AB`, `demote_to`,
`demote_reasons`), `history_match`, `url_flags`, and `has_body`/`has_regional`/
`has_state_watch` flags — everything needed to select, nothing you don't. **Do not read
the return files to browse candidates**; select from the triage. If intake errors, fall
back to reading the return files directly and triaging by hand (note it in the run log).

Selection rules (you, on the triage):

1. **Honor the intake verdicts.** Dropped candidates are gone. A `demote_to: "C"`
   candidate may not occupy an A/B slot — low-priority filler only; never override a
   sourcing demotion just to fill a slot. Prefer `primary_source_url` when citing.
2. **Claim-level scrutiny.** `passes_AB` rates the story, not every figure in it. Read
   each candidate's `confidence` note: if a load-bearing figure (casualty count, dollar
   figure, violation tally) rests on a single partisan/interested source, it must be
   attributed inline in the body and absent from headline/toc_hook. The agents were
   briefed to do this; where a confidence note suggests they didn't, **Read that one
   return file** and prepare an `overrides` fix for Phase 3 — never ship a single-source
   partisan number as bare fact.
3. **History dedup.** Non-null `history_match` = already covered unless there's a
   substantive new development. ≈0.85+ similarity (esp. `same_category`) → drop, or run
   only as an explicit `update_badge` "Update — …" naming what's new. Borderline
   (~0.6–0.7) → keep only if today genuinely advances the thread, prefer update framing.
4. **Framing-consistency check** (ongoing-conflict stories; the fuzzy `history_match`
   can miss a contradicting entry). Compare each ongoing-conflict candidate's framing of
   dates/actors/status against the `framing_ground_truth` headlines from Phase 0. If it
   reverses an established framing (e.g. "defying a ceasefire" vs. "ceasefire began"),
   do not ship the contradiction — reconcile to the established framing or drop. A
   reversal is a red flag, not an update.
5. **Cross-agent dedup.** Adjudicate every `dedup_pairs` entry (and obvious duplicates
   it missed): collapse same-event candidates, keep the better-sourced one. Wild-card
   vs. core-topic duplicates: the core story wins; wild card moves to its next candidate.
6. **Count + balance** — successfully returned topics only; wild card excluded. Counts
   come from `config.story_count` (`min`/`max`/`per_topic_cap`):
   - Floor: best story from each topic with ≥1 qualifying A/B candidate; backfill with
     the next-best Tier B from the strongest remaining topic.
   - Promote remaining A / strong B that are genuinely worth reading. Stop at `max`.
   - **Per-topic cap applies.** `min`–`max` is a ceiling, not a target — prefer `min`
     strong stories over `max` mediocre ones; any topic may take 0 slots on a quiet
     day. Fewer topics returned = lower floor; note reduced coverage in the run log.
7. **On Your Radar** (`pending_topics`, by tag): `[research]` = full card; `[watch]` =
   only if something happened in the last 48h, else skip silently; `[my take]` = lead
   with the user's quoted words, then researched context. OYR cards are **additive** —
   never count toward the `min`–`max`. You author these yourself (they have no agent
   return) as `inline` stories in Phase 3: `id` `oyr1`/`oyr2`/…, `"oyr": true,
   "category": "On Your Radar"`, and include a `core_topic` for history.
8. **Wild card — pick exactly one, or none.** Rank the wild-card agent's surviving
   candidates by: (a) 👍 `liked_history` pattern fit — the primary signal, beats equal
   tier; (b) the 👎 `active_feedback` screens (hard filter = drop, soft deprioritize =
   only if nothing better, conditional = apply it); (c) sourcing (promotion guard as
   usual; ship a demoted candidate only if nothing better qualifies and it is genuinely
   notable, confidence note carried into the body); (d) freshness — last ~24h, hard
   requirement. Additive like OYR; per-topic cap doesn't apply; `id` `wc1`. If nothing
   survives, ship without a wild card and say why in the run log.

---

## PHASE 3 — Assemble → build → validate (bodies never enter your context)

Write a compact **selection JSON** to the day's `state/.../selection.json` — picks by
candidate id plus your inline OYR cards (exact shape documented at the top of
`_pipeline/assemble_stories.py`; `framing` = your 2–3 sentence editor's note, not a
contents list; `update_badge` and `overrides` per story as needed). Then:

```bash
python3 "$PROJECT/_pipeline/assemble_stories.py" \
  --in "$PROJECT/_pipeline/state/$(date +%F)/selection.json" \
  --today $(date +%F)
```
It merges each pick's full record (body, regional, divergence, state_watch) from the
return files, auto-sets `"wildcard": true` for the Wild Card topic, orders OYR-first /
wild-card-last, enforces max 1 wild card, and writes `stories.json` +
`history-entries.json` into the state dir.

- **Exit 1** = problems (bad pick id, missing body/hook, duplicate ids) — fix the
  selection and re-run.
- **Overlap-tripwire warnings** (a state-watch story's body vs. its State Watch callouts
  sharing a number or place name): Read that one candidate's return file, resolve the
  overlap per the division-of-labor rules in `_pipeline/briefing/enrichment_state_watch.md`
  (body = national thesis; State Watch = focus-market detail + concrete move; zero
  repeated facts), apply via `overrides`, re-run assemble.

Build and validate:
```bash
python3 "$PROJECT/_pipeline/build_digest.py" \
  --in "$PROJECT/_pipeline/state/$(date +%F)/stories.json"
python3 "$PROJECT/_pipeline/validate_digest.py" \
  --html "$PROJECT/$(date +%F)-news-digest.html" \
  --date $(date +%F)
```
Validation checks the regular-card count against `config.story_count` (OYR + wild card
excluded, max 1 wild card), TOC anchors, category values, the feedback script, and
`DIGEST_DATE`. **If it fails, fix the selection/stories JSON and re-run assemble +
build + validate until it passes**; if truly unfixable, record it prominently in the
run log — never silently ship a failed digest. If `build_digest.py` or `template.html`
is missing/broken, fall back to hand-writing HTML from the most recent digest and note
the fallback.

---

## PHASE 4 — Atomic writeback

1. **History:**
   ```bash
   python3 "$PROJECT/_pipeline/append_history.py" \
     --new "$PROJECT/_pipeline/state/$(date +%F)/history-entries.json"
   ```
   (Idempotent; entries were generated by assemble from what actually shipped.)
2. **Prune history** (keeps the file bounded):
   ```bash
   python3 "$PROJECT/_pipeline/prune_history.py" --keep-days 90
   ```
3. **Daily-topics inbox:** move each pending entry you actually covered from `## Pending`
   to `## Processed`, appending ` ✓ covered: YYYY-MM-DD`. Skip entries whose topic
   failed.
4. **Run log** `YYYY-MM-DD-run-log.md` next to the HTML: start by copying the day's
   `state/.../intake-log.md` (the mechanical tables are pre-written — do not re-type
   them), then append only the editorial sections: resume point if any, per-agent
   outcomes (first try / retried / dropped), your selection decisions with reasons,
   which wild-card candidate won and which 👍/👎 entries influenced it (or why none
   shipped), validation result, fallbacks taken.
5. **Clean old state dirs:**
   ```bash
   python3 "$PROJECT/_pipeline/checkpoint.py" clean --keep-days 2
   ```
6. **Present the digest** via `mcp__cowork__present_files` (clickable card, one short
   line of accompanying text, no story-by-story summary) — never end by only stating a
   path. Then **release the lock — always, last thing, on every exit path** once held:
   ```bash
   python3 "$PROJECT/_pipeline/run_lock.py" release
   ```

---

## Carry-over invariants (do not lose these in any future edit)
- **Token discipline:** agent gathering rules live in `_pipeline/briefing/*.md` and are
  stamped into per-agent prompt files by `build_context.py --emit-agent-prompts` — edit
  the briefing files, don't re-inline the rules here. Agent returns and story bodies
  live in files; the orchestrator selects from the bodiless triage and reads an
  individual return file only to fix a flagged problem. `assemble_stories.py` — not the
  orchestrator — merges bodies into stories.json and emits history entries.
- **Checkpoints:** every phase writes into `_pipeline/state/YYYY-MM-DD/` (context+prompts
  → returns → triage → stories). Any run — first attempt or retry — resumes from the
  furthest checkpoint (PRE-PHASE 0c) rather than redoing completed phases. A FAILED
  marker (`checkpoint.py mark-failed`) means a systematic failure: don't blindly rerun.
  Some mounted/synced folders block unlink, so `clean` empties old files rather than
  deleting — 0-byte artifacts count as absent.
- **Agent blips** get exactly one in-run re-launch with a reduced budget
  (`config.agent_retry`), then the topic is dropped gracefully. Never infinite-retry,
  never abort the run for one agent.
- Dedup uses `date` (never `date_shown`), 30-day window; injected lists are capped
  (`dedup_inject_cap`) but intake checks the FULL window — capping never weakens dedup.
- Dedup entries are **framing ground truth**: never ship a story that reverses an
  established date/actor/status framing (check `framing_ground_truth` from Phase 0, not
  just the fuzzy `history_match`); flag genuine reversals, don't invent reconciliations.
- Story-level sourcing ≠ claim-level: single-partisan-source figures must be attributed
  inline in the body, kept out of headline/toc_hook unless independently corroborated,
  and named in `confidence`; the orchestrator enforces via `overrides`.
- A/B tiers require ≥2 named outlets or a primary source; aggregators and
  forward-looking single-source claims cap at Tier C; URLs must be deep links to dated
  articles; the WebSearch 400s blocklist never goes in `allowed_domains`.
- State-watch body vs. State Watch callout division of labor (national thesis vs.
  focus-market action, zero shared facts, ends with the move, every state in
  `config.focus_states` covered or say why not) — full rules in
  `briefing/enrichment_state_watch.md`; `assemble_stories.py`'s tripwire flags
  violations; the focus markets come from `config.focus_state_markets`.
- Per-topic cap and the `min`–`max` range come from `config.story_count`; the range is a
  ceiling, not a target, and any topic may take 0 slots. OYR is additive (`"oyr": true`).
  **Wild Card** is additive, 0-or-1 per day, drawn from outside the core beats per
  `config.wildcard.definition`, steered by 👍 `liked_history` then 👎 `active_feedback`,
  `id` `wc1` / `category` "Wild Card" / `"wildcard": true`, skipped cleanly when nothing
  qualifies (log why). Updates require an explicit `update_badge`.
- `news_feedback.md` has two signal sections — "Active preferences" (👎) and "Liked
  stories (👍 history)"; `build_context.py` injects 👎 rules into every agent prompt and
  👍 history into the wild-card prompt.
- HTML is assembled by `build_digest.py` from `template.html` only; the feedback
  script block + `DIGEST_DATE` are template-guaranteed; validate confirms.
- Surface the digest via `present_files`; PRE-PHASE 0 guard semantics (0a exit 100 =
  present + stop, no lock; 0b exit 101 = stop without releasing; otherwise always
  release last — the lock is released by overwriting, never deleting, `.run.lock`).
