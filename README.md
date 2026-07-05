# Daily News Digest

A personal, self-updating **daily news digest** that runs unattended every day and produces a
single, clean HTML page across the topics *you* care about. It's built to run inside
[Claude](https://claude.ai) (Cowork mode / Claude Code): an orchestrator skill fans out parallel
research agents, dedups against a rolling history, applies your learned preferences, and assembles
the page from a template — all deterministically enough to run at 1am without you watching. It is
optimized to reduce token usage, and if you schedule it to run at the middle of the night, the
session limit will be gone by time you get the daily digest. Delivery not included - the digest
is a html file in the project, if you want you can add email delivery etc.

> This repo ships the **engine and templates** only. Your personal config, preferences, and every
> generated digest stay on your machine and are git-ignored — see [Privacy](#privacy).

## What it does

- **Multi-topic coverage.** One parallel research agent per topic (the example config ships
  World News, US Politics, Technology, Business & Markets, and Science — swap in whatever *you*
  pick), each returning compact, deep-linked, source-graded story candidates.
- **Deterministic quality gates in code, not AI vibes.** Python helpers handle dedup against a 30-day
  history, source-quality screening (primary source > named wire > aggregator), cross-agent
  duplicate detection, and final HTML validation.
- **Learns your taste.** Thumbs-up / thumbs-down on stories feed `news_feedback.md`, which steers
  future selection (and a daily finance "Wild Card" pick).
- **On Your Radar.** Drop a line into `daily-topics.md` (even from your phone — see
  `SETUP-ad-hoc-topics.md`) and that topic gets researched into the next morning's digest.
- **Regional "State Watch" (optional).** For beats like real estate, national analysis in the body
  plus local, actionable callouts for the specific markets you follow.
- **Runs unattended, degrades gracefully.** A duplicate-output guard and a concurrent-run lock keep
  scheduled and manual runs from colliding; if one topic agent fails, the digest still ships and
  logs the gap. Schedule it to run twice a night, 30 minutes apart, to work around transient API
  failures. Remember to allow Claude to wake up your computer.

## Repo layout

```
.
├── SKILL.md                    # The orchestrator skill (invoke this to produce a digest)
├── setup-news-digest/          # First-run onboarding skill (source)
├── setup-news-digest.skill     # Same skill, packaged for one-click install
├── _pipeline/
│   ├── config.example.json     # Copy → config.json and edit (or let setup do it)
│   ├── template.html           # All digest HTML/CSS lives here (never hand-write markup)
│   ├── build_context.py        # Emits per-agent prompt files (dedup/feedback/topics context)
│   ├── intake_candidates.py    # Source screening + dedup verdicts → bodiless triage
│   ├── assemble_stories.py     # Merges picked candidates' bodies → stories JSON
│   ├── build_digest.py         # Renders stories JSON → the day's HTML
│   ├── validate_digest.py      # Post-build structural checks
│   ├── append_history.py / prune_history.py     # Idempotent, bounded history writeback
│   ├── check_existing_digest.py / run_lock.py   # Run guards
│   ├── checkpoint.py           # Phase checkpoints → retries resume, not re-run
│   ├── briefing/               # Agent briefing templates (the long-form gathering rules)
│   └── state/                  # Created at runtime: per-day prompts/returns/checkpoints (git-ignored)
├── news_feedback.example.md    # Copy → news_feedback.md (your learned preferences)
├── daily-topics.example.md     # Copy → daily-topics.md (your ad-hoc topic inbox)
└── SETUP-ad-hoc-topics.md      # Optional: phone-button workflow to queue topics on the go
```

Generated digests (`YYYY-MM-DD-news-digest.html`) and run logs are written to the repo root and
kept out of git by `.gitignore`.

## Setup

**The easy way — run the onboarding skill.** Install `setup-news-digest.skill` (or point Claude at
the `setup-news-digest/` folder) and ask Claude to *"set up my news digest."* It interviews you for
your branding, topics, sources, any regional focus, timezone, and delivery time, then generates your
`config.json`, `news_feedback.md`, and `daily-topics.md` and offers to schedule the daily run.

**The manual way.**

1. Copy the templates:
   ```bash
   cp _pipeline/config.example.json _pipeline/config.json
   cp news_feedback.example.md news_feedback.md
   cp daily-topics.example.md daily-topics.md
   ```
2. Edit `_pipeline/config.json` — set `digest_title`, replace `topics` with yours (give each a
   `key`, kebab `slug`, `definition`, and a `category_colors` entry), set `digest_timezone`, and
   either fill in `focus_states` / `focus_state_markets` for the regional angle or set them empty.
3. Produce a digest by invoking the `daily-news-digest` skill (`SKILL.md`). To automate it, ask
   Claude to schedule it (in Cowork: *"run my daily news digest every morning at 6am"* creates a
   scheduled task; in Claude Code, use cron or any scheduler to invoke the skill).

**Requirements:** Claude (Cowork mode or Claude Code) and `python3` (standard library only — no
third-party packages).

**Folder resolution (portable by design).** Nothing stores an absolute path. Every `_pipeline`
script finds the project folder in this order: an explicit `--folder` argument → the
`$DAILY_NEWS_FOLDER` environment variable → the parent of the `_pipeline/` directory it lives in.
Clone the repo anywhere, under any name, and it just works; set `$DAILY_NEWS_FOLDER` only if you
invoke the scripts from a copy that lives outside the project folder.

## How it works (the short version)

`SKILL.md` is the orchestrator, and the pipeline is file-based and token-lean: (0) run the
duplicate/concurrency guards and check for a resumable checkpoint, (1) `build_context.py` stamps a
self-contained prompt file per topic from the `briefing/` templates (dedup history, feedback rules,
sources, search budget baked in), (2) one research agent per topic runs in parallel and writes its
candidates to a return file, (3) `intake_candidates.py` reduces the returns to a bodiless triage —
source screening, dedup verdicts — from which the orchestrator makes the editorial selection,
(4) `assemble_stories.py` merges the picked candidates' full text (story bodies never pass through
the orchestrator's context) and `build_digest.py` renders the page from `template.html`, validated
before shipping, then (5) history, the topic inbox, and the run log are written back atomically.
Every phase checkpoints into `_pipeline/state/<date>/`, so a retry resumes where the last attempt
stopped instead of re-running. The design rule throughout: **emit content, not boilerplate, and
never abort the whole digest because one piece failed.**

## Privacy

Everything personal or generated is git-ignored so it never leaves your machine: your real
`config.json`, `news_feedback.md`, `daily-topics.md`, `story-history.json`, `_pipeline/state/`, and
every `*-news-digest.html` / `*-run-log.md`. The committed repo contains only the engine, the
`*.example` templates, and the two skills. Before pushing, double-check `git status` shows none of
your personal files staged.

## License

MIT — see [LICENSE](LICENSE).
