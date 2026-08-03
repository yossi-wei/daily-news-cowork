---
name: setup-news-digest
description: First-run onboarding for the Daily News Digest project. Use when a new user has just cloned/downloaded the daily-news-digest repo and wants to set it up — asks them for their name/branding, the topics they care about, their sources, any regional focus, and their preferred delivery time, then generates their personal config.json, news_feedback.md, and daily-topics.md and offers to schedule the daily run. Triggers on "set up my news digest", "onboard me", "configure the daily digest", "I just cloned the news digest", or "get the digest running".
---

# Daily News Digest — Setup / Onboarding

You are onboarding a **new user** to the Daily News Digest project. Your job: interview them
briefly, then generate their three personal files so the digest can run. Be warm and quick —
this should take a couple of minutes, not an interrogation.

## 0. Locate the project folder

The project folder is the one containing `_pipeline/` (with `config.example.json`,
`build_digest.py`, etc.) and the canonical `SKILL.md`. Confirm you can see it. Call it
`$PROJECT` (substitute its real absolute path wherever `$PROJECT` appears below). If you
can't find it, ask the user to point you at the folder where they cloned the repo.
Everything you write goes into `$PROJECT`.

Find the config template in this order (use the first that exists):
1. `$PROJECT/_pipeline/config.example.json` (ships with the repo — preferred)
2. `references/config.template.json` (bundled with this skill — fallback)

Likewise for `news_feedback.example.md` / `daily-topics.example.md` (repo) or the
`references/*.template.md` copies bundled here.

## 1. Interview the user (use the AskUserQuestion tool)

Ask these as multiple-choice where possible so it's fast. Combine into as few rounds as makes
sense; always allow a custom "Other" answer.

1. **Branding** — "What should we call your digest?" (e.g. "Alex's Daily Digest", "Morning
   Brief"). This becomes `digest_title`. The last word is shown in an accent color.
2. **Topics** — "Which beats do you want covered each day?" Offer common options and let them
   multi-select or add their own. Good starting menu: World / Middle East, US Politics &
   Policy, AI & Tech, Business & Markets, Science, Space, Climate & Energy, Health, Sports,
   Local news. Aim for **3–6 topics** — enough for variety, not so many the digest thins out.
3. **Regional / local angle (optional)** — "Do you want a local 'watch' angle on any topic —
   e.g. how national real-estate or policy news hits specific cities/counties you follow?"
   If yes, collect the states and the specific city/county markets. If no, you'll drop the
   `focus_states` / `focus_state_markets` blocks and any state-watch enrichment.
4. **Sources (optional)** — "Any must-have or must-avoid sources?" Defaults are fine; capture
   overrides into `official_sources` / `supplemental_sources`.
5. **Delivery** — "What time each morning, and what timezone?" Capture the time (for scheduling)
   and set `digest_timezone` to their IANA zone (e.g. `America/New_York`, `Europe/London`).
6. **Wild Card** — "Want a daily 'Wild Card' — one surprising finance/markets story outside your
   main topics?" Sets `wildcard.enabled`.
7. **Figures (optional)** — "Want the digest to draw a small chart when a story is genuinely
   about numbers? It's off by default, and most days will still have none." If they say yes,
   ask which of their beats should be eligible. Recommend starting with **one or two**
   data-heavy beats rather than all of them: the point of the feature is scarcity, and a
   digest where most stories carry a chart signals nothing. Sets `figures.enabled` and each
   chosen topic's `"figures": "chart"`. If they say no, leave everything as the template has
   it — the feature then costs nothing at all, not even prompt tokens.

## 2. Generate `_pipeline/config.json`

Start from the config template (found in step 0) and apply their answers:

- Set `digest_title` to their branding. Remove the `_TEMPLATE_NOTE` key.
- Replace the `topics` array with **their** chosen topics. For each topic set:
  - `key` (display name), `slug` (kebab-case, e.g. "us-politics"),
  - `definition` (a 1–2 sentence scope written in the same style as the examples — say what's
    in and out of scope so the gathering agents stay on-beat),
  - `enrichment`: use `"regional+divergence"` for most beats; use `"state-watch"` only for a
    beat that should get the local-market treatment from step 1.3.
- **Colors:** ensure every topic slug has an entry in `category_colors`. Reuse the example
  palette; for extra topics pick a distinct, readable border/badge trio (keep the badge text
  dark on a light badge background). Leave `oyr` and `wildcard` colors as-is.
- **Regional angle:** if they wanted it, set `focus_states` to their states and
  `focus_state_markets` to `{ "State": ["City — County", …] }`. If they didn't, set
  `focus_states` to `[]`, `focus_state_markets` to `{}`, and make sure no topic uses
  `"state-watch"` enrichment.
- Set `digest_timezone`, `wildcard.enabled`, and any source overrides.
- **Figures:** if they said yes in step 1.7, set `figures.enabled` to `true` and give each
  chosen topic `"figures": "chart"`. Every other topic keeps `"figures": "none"`. If they said
  no, change nothing — the template already ships with `figures.enabled` false and every topic
  at `"none"`.
- **Regional tags:** if you set `focus_states`, give each entry a short label in
  `focus_state_tags` (e.g. `"Illinois": "IL"`) — that is what appears in the State Watch pill
  and in the agents' return schema. Unmapped entries fall back to the full name.
- Keep all the machinery keys (dedup windows, `source_screen`, `agent_*`, allow/block lists,
  `story_count`, etc.) unless the user asked to change them — they're sensible defaults.

Validate it: `python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$PROJECT/_pipeline/config.json"`.

## 3. Seed the state files (only if absent — never overwrite)

- If `$PROJECT/news_feedback.md` does not exist, copy the feedback template there.
- If `$PROJECT/daily-topics.md` does not exist, copy the daily-topics template there.
- Leave `story-history.json` alone; the first run creates it.

## 4. Smoke-test the build

Confirm the engine renders with their config using a tiny throwaway stories file, then delete
the throwaway HTML:

```bash
cd "$PROJECT"
python3 - <<'PY' > /tmp/_smoke_stories.json
import json
print(json.dumps({"date":"2099-01-01","framing":"Setup smoke test.","source_count":"1+",
 "stories":[{"id":"s1","category":"__FIRST_TOPIC_KEY__","headline":"Test","toc_hook":"test",
 "sources":["Example"],"body":["ok"]}]}))
PY
python3 _pipeline/build_digest.py --in /tmp/_smoke_stories.json --out /tmp/_smoke_out.html
grep -m1 -F "__DIGEST_TITLE_VALUE__" /tmp/_smoke_out.html
```

Replace `__FIRST_TOPIC_KEY__` with the exact `key` of their first topic and
`__DIGEST_TITLE_VALUE__` with their `digest_title` string. If the grep finds their branding
in the page title and there was no build error, the config is wired correctly. Report
success; don't leave the smoke-test HTML in the project folder.

## 5. Offer to schedule the daily run

Ask if they'd like the digest to run automatically each morning at the time from step 1.5. If
yes, create a scheduled task that runs the `daily-news-digest` skill (the canonical `SKILL.md`
in `$PROJECT`) on a daily cron at that local time. Mention they can drop `[research]` /
`[watch]` / `[my take]` lines into `daily-topics.md` anytime to steer a given day's digest, and
that thumbs-up/down on stories teaches `news_feedback.md` over time.

## Wrap up

Summarize in 2–3 lines what you configured (branding, topics, timezone, schedule) and point
them at their new `config.json`. Keep it short — they can read the file themselves.
