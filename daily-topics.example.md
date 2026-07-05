# Daily Topics Inbox

Claude reads this file at the start of every digest run. Entries under `## Pending` dated today or earlier are researched and included as an **"On Your Radar"** section in that day's digest. After processing, Claude moves each entry to `## Processed`.

> This is the EXAMPLE template. The `setup-news-digest` skill copies it to `daily-topics.md` (gitignored so your personal queue stays private). See `SETUP-ad-hoc-topics.md` for an optional phone-button workflow to append entries on the go.

## Entry format

Each entry is a single line:

```
- YYYY-MM-DD | tag | your topic or question
```

**Tags:**
- `[research]` — full research, written as a complete story card (same depth as regular digest stories)
- `[watch]` — check for updates in the last 48 hours; include only if something newsworthy happened
- `[my take]` — lead with your perspective (quoted from the entry), backed by research Claude finds

**Example entries:**
```
- 2026-05-28 | [research] | State SB 1234 landlord-tenant bill — what passed?
- 2026-05-28 | [watch] | That AI company's IPO timeline — any updates?
- 2026-05-28 | [my take] | The photonics story was undercovered — worth a deeper look
```

---

## Pending

<!-- New entries go here (one per line, in the format above) -->


---

## Processed

<!-- Claude moves entries here after including them in a digest, with ✓ and the date covered -->
