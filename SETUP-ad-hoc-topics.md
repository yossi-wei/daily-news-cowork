# Ad-hoc Daily Topics — Setup Guide

Two things to set up: (1) sync the Daily News folder to OneDrive, (2) a Power Automate button on your Android phone.

---

## Part 1 — Sync the Daily News folder to OneDrive (Mac, one-time)

1. Open the **OneDrive** app on your Mac (menubar cloud icon → gear → Settings).
2. Go to **Account → Choose folders** (or **Sync and backup → Manage backup**).
3. Under **Add a folder to sync**, click **Add new sync location** and navigate to your local
   **Daily news** project folder (wherever you cloned/keep this repo).
4. Click **Select Folder**. OneDrive will begin syncing. Wait for the sync icon on the folder to show a green checkmark.
5. On your Android phone, open the **OneDrive** app and verify the `Daily news` folder appears under **My Files**.

That's it — the `daily-topics.md` file is now accessible from any device.

---

## Part 2 — Power Automate flow (Android button → OneDrive file)

### What you'll need
- Microsoft account (you already have one via OneDrive)
- **Power Automate** app installed on Android (free, search "Microsoft Power Automate")
- **Power Automate** open on a browser at make.powerautomate.com to build the flow

### Build the flow (do this on your laptop browser, ~15 minutes)

**Step 1 — Create a new flow**

1. Go to make.powerautomate.com and sign in.
2. Click **+ Create** → **Instant cloud flow**.
3. Name it: `Add to Daily Digest`.
4. Choose trigger: **Manually trigger a flow**. Click **Create**.

**Step 2 — Configure the trigger inputs**

1. Click the trigger card **Manually trigger a flow** to expand it.
2. Click **+ Add an input**:
   - Type: **Text** — Label: `Topic`
3. Click **+ Add an input** again:
   - Type: **Drop down** — Label: `Tag`
   - Options: `[research]`, `[watch]`, `[my take]`

**Step 3 — Get the current file content**

1. Click **+ New step**.
2. Search for **OneDrive for Business** (or **OneDrive** if personal account) → action: **Get file content**.
3. In **File**, click the folder icon and navigate to:
   `Daily news / daily-topics.md`
4. Leave everything else default.

**Step 4 — Build the new line to append**

1. Click **+ New step** → search **Data Operation** → **Compose**.
2. In the **Inputs** box, enter this expression (click the expression editor `fx`):

```
concat(body('Get_file_content'), '\n- ', formatDateTime(utcNow(), 'yyyy-MM-dd'), ' | ', triggerBody()['text_1'], ' | ', triggerBody()['text_2'])
```

> This appends a new line in the correct format:
> `- 2026-05-29 | [research] | your topic here`

**Step 5 — Write the updated content back**

1. Click **+ New step** → **OneDrive for Business** → **Update file**.
2. In **File**, select the same `daily-topics.md` file as Step 3.
3. In **File Content**, click **Dynamic content** and select **Outputs** from the Compose step.

**Step 6 — Save and test**

1. Click **Save** (top right).
2. Click **Test** → **Manually** → **Run flow**.
3. Enter a test topic and tag. Click **Run flow**.
4. Check `daily-topics.md` in OneDrive — the new line should appear under `## Pending`.

---

## Part 3 — Add to Android home screen

1. Open the **Power Automate** app on Android.
2. Tap **My flows** → find **Add to Daily Digest**.
3. Tap the three-dot menu → **Add to home screen** (or long-press the flow → **Create shortcut**).
4. A shortcut icon appears on your home screen.

**To use it:** tap the icon → fill in Topic and Tag → tap **Run** → done. The entry appears in `daily-topics.md` within seconds and will be picked up at the next morning's digest run.

---

## Part 4 — macOS wake schedule (prevents missed digest runs)

Run this command once in Terminal to wake your Mac every morning at 4:50 AM before the digest fires at ~6:15 AM:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 04:50:00
```

Enter your Mac password when prompted. This takes effect immediately and persists across reboots. Your Mac will wake from sleep at 4:50 AM, run the digest, and can go back to sleep normally.

To verify it's set:
```bash
pmset -g sched
```

To remove it later if needed:
```bash
sudo pmset repeat cancel
```

---

## How the full flow works end-to-end

```
You (Android)
  → tap home screen shortcut
  → type topic + select tag
  → Power Automate appends line to daily-topics.md
  → OneDrive syncs to Mac
  → Next morning: scheduled task reads daily-topics.md
  → Researches your topic, writes a full story card
  → Appears in digest under "📌 On Your Radar" section
  → Entry moved to ## Processed with ✓ marker
```

---

## Entry format reference

```
- YYYY-MM-DD | [research] | Illinois SB 1234 landlord-tenant bill — what passed?
- YYYY-MM-DD | [watch] | Anthropic IPO timeline — any movement?
- YYYY-MM-DD | [my take] | The Penn photonics story was undercovered — dig deeper
```

**Tags:**
- `[research]` — full investigation, written as a complete story card
- `[watch]` — check for last-48-hour updates; skip if nothing new
- `[my take]` — lead with your words, backed by research
