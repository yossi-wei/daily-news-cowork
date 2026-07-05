# News Feedback & Preferences

This file is Claude's learned preferences for the daily news digest, built from your 👍/👎 submissions on past digests. Thumbs-down entries land under **Active preferences** (what to avoid); thumbs-up entries land under **Liked stories (👍 history)** (what to pull toward).

It is intended to be fully auditable: every entry is dated, cites the story that triggered it, quotes your own words, and states the resulting rule or pattern.

**How this file is used:** the daily-news-digest scheduled task reads this file before picking stories. Active-preference entries act as soft filters — matching candidates are deprioritized or replaced. Liked-stories entries act as positive weights — candidates matching a liked pattern are preferred, and they are the primary steering signal for the daily finance **Wild Card** pick.

> This is the EXAMPLE template. The `setup-news-digest` skill copies it to `news_feedback.md` (which is gitignored so your personal preferences stay private). Start it empty — it fills in as you react to digests.

---

## Active preferences

*(No entries yet. When you thumbs-down a story in a digest, a new entry will land here.)*

---

## Liked stories (👍 history)

*(No entries yet. When you thumbs-up a story in a digest, a new entry will land here. These weight story selection toward similar patterns — especially the daily Wild Card.)*

### Liked-entry template

```
### YYYY-MM-DD — Short pattern title
- **Story:** [Story headline] (digest date, category)
- **Your words:** "…verbatim from the thumbs-up note, or (no note)…"
- **Pattern:** One sentence describing the general kind of story to favor going forward.
- **Confirmed:** YYYY-MM-DD[, YYYY-MM-DD…] — dates of later 👍s matching this same pattern.
- **Status:** active | retired
```

---

## Entry template

```
### YYYY-MM-DD — Short rule title
- **Triggered by:** [Story headline] (digest date)
- **Category:** <one of your topics> | Other
- **Your words:** "…verbatim from the thumbs-down text…"
- **Rule:** One sentence describing what to avoid or deprioritize going forward.
- **Scope:** How strict — hard filter, soft deprioritize, or conditional (e.g. "only when X").
- **Status:** active | superseded | retired
```

---

## Retired / superseded entries

*(Entries moved here when a newer preference overrides them, or when you say to drop a rule.)*
