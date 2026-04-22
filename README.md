# Canvas Manager

Automatically fetches Canvas LMS assignments and Google Calendar events, builds an AI-powered daily study schedule, and sends email + SMS reminders — no Twilio required. Uses the Gmail API for all notifications and Google Gemini for smart scheduling.

---

## Features

- Fetches upcoming assignments from Canvas and events from Google Calendar
- Classifies deadlines into types (`class`, `assignment`, `personal`, `study`, `other`) with Gemini AI
- Generates a daily study plan around your habits (wake/sleep, peak focus hours, priority order, exam prep)
- Improves and optimizes the generated schedule with Gemini
- Natural-language schedule editing: `mana schedule "add gym at 3pm"`
- Sends formatted HTML email and SMS reminders for deadlines or today's schedule
- Shows `✓ submitted` indicator for already-submitted Canvas assignments
- Daily cron job syncs, plans, and reminds automatically

---

## Requirements

- Python 3.10+
- A Canvas account with API access
- A Gmail account (used to send email and SMS)
- A Google Cloud project with **Gmail API** and **Google Calendar API** enabled
- A US phone number and carrier name
- (Optional but recommended) A [Google Gemini API key](https://aistudio.google.com/app/apikey) for smart classification, duration estimation, and schedule improvement

---

## Installation

```bash
pip install .
```

This installs the `mana` command globally.

---

## New User Walkthrough

### Step 1 — Get your Canvas API token

1. Log in to Canvas (e.g. `https://canvas.youruniversity.edu`)
2. Go to **Account → Settings**
3. Scroll to **Approved Integrations → New Access Token**
4. Give it a name, click **Generate Token**, and copy it (shown only once)

---

### Step 2 — Set up Google Cloud credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable **Gmail API** and **Google Calendar API**
3. Go to **APIs & Services → OAuth consent screen** → External; add your Gmail as a test user
4. Go to **Credentials → Create Credentials → OAuth client ID → Desktop app**
5. Download the JSON file and rename it to exactly `credentials.json` in the project directory

> The first time you run `sync`, a browser window opens for Google authorization. A `token.json` is created and reused automatically. **Never commit either file.**

---

### Step 3 — Run interactive setup

```bash
mana setup
```

Walks you through all credentials interactively, validates them live, and writes `.env`. Optionally enter a Gemini API key to enable AI features.

---

### Step 4 — Set your schedule habits

```bash
mana habits
```

Configure wake/sleep times, peak focus hours, break length, priority order, and exam prep settings (how many study blocks to place N days before an exam). These drive the AI scheduler.

---

### Step 5 — Sync your deadlines

```bash
mana sync
```

Fetches assignments from Canvas and events from Google Calendar, Gemini-classifies them, and caches them locally.

---

### Step 6 — Generate today's plan

```bash
mana plan
```

Builds a study schedule for today using your habits. Gemini estimates how long each task will take and then improves the final block layout. Use `schedule` to tweak the result.

---

## Commands

### `setup`
Interactive first-time configuration. Validates credentials live and writes `.env`.

```bash
mana setup
```

---

### `habits`
Set or review schedule preferences: wake/sleep, peak focus hours, priority order, exam prep.

```bash
mana habits
```

---

### `sync`
Fetch the latest assignments from Canvas and Google Calendar, classify with Gemini, and save locally.

```bash
mana sync
mana sync --no-gcal   # skip Google Calendar
```

---

### `list`
Show upcoming deadlines from the local cache, split into assignments, classes, and personal items.

```bash
mana list
mana list --days 7
```

---

### `plan`
Generate (or view) the study schedule for a day. Gemini estimates task durations and optimizes block placement.

```bash
mana plan                        # today
mana plan --date 2026-05-01
mana plan --overwrite            # clear and replan from scratch
mana plan --export               # write .ics file
mana plan --export --out ~/plan.ics
```

---

### `todo`
Show a summary count of upcoming deadlines. Add flags to see detail tables or send notifications.

```bash
mana todo                        # "3 assignments, 2 classes, 1 personal item"
mana todo --assignments          # show assignment table
mana todo --classes              # show class table
mana todo --personal             # show personal table
mana todo --email                # send email
mana todo --sms                  # send SMS
mana todo --email --sms          # send both
mana todo --days 7               # override lookahead window
mana todo --to-email you@example.com
```

---

### `send`
Send today's block schedule (from `plan`) via email and/or SMS.

```bash
mana send                        # email + SMS (default: both)
mana send --email                # email only
mana send --sms                  # SMS only
mana send --preview              # print without sending
mana send --date 2026-05-01
mana send --to-email you@example.com
```

---

### `schedule`
Modify today's schedule with a natural-language command, powered by Gemini.

```bash
mana schedule "add gym from 3pm to 4pm"
mana schedule "move the ML homework block to 2pm"
mana schedule "delete the study block"
mana schedule "clear everything after 6pm"
mana schedule "rename HW5 to Problem Set 5"
mana schedule "add a 30-min break at noon" --date 2026-05-01
mana schedule "add lunch at noon" --preview   # show changes without saving
```

> Requires a Gemini API key (set during `setup` or via `GEMINI_API_KEY` in `.env`).

---

### `import-ical`
Import a `.ics` calendar file and merge it with Canvas deadlines.

```bash
mana import-ical ~/Downloads/calendar.ics
mana import-ical   # prompts for the file path
```

---

### `setup-cron`
Install a daily cron job that runs `sync` then `todo --email --sms` at a chosen time.

```bash
mana setup-cron
mana setup-cron --time 09:30
```

Logs are written to `~/.canvas_manager.log`.

---

### `clear-cache`
Delete the local deadlines cache.

```bash
mana clear-cache
```

---

## Gemini AI integration

Three Gemini calls power the AI features. All require `GEMINI_API_KEY` in `.env`; without it, the tool falls back gracefully to rule-based behavior.

| Call | When | What it does |
|---|---|---|
| `classify_events` | `sync` | Corrects type labels on incoming deadlines |
| `estimate_durations` | `plan` | Estimates realistic work time per task |
| `improve_schedule` | `plan`, `schedule` | Optimizes block placement and pacing |
| `parse_schedule_command` | `schedule` | Interprets free-text commands into block edits |

Model: `gemini-2.5-flash-lite` (configurable via `GEMINI_MODEL` in `.env`).

---

## Daily automation

Once `setup-cron` is installed, every morning at your chosen time:

```
mana sync && mana todo --email --sms
```

To also send the day's study schedule, add a second cron line:

```
mana plan && mana send
```

---

## Supported SMS carriers

| Value | Carrier |
|---|---|
| `tmobile` or `t-mobile` | T-Mobile |
| `att` or `at&t` | AT&T |
| `verizon` | Verizon |
| `sprint` | Sprint |
| `uscellular` | US Cellular |
| `boost` | Boost Mobile |
| `cricket` | Cricket Wireless |
| `metro` | Metro by T-Mobile |

---

## File reference

| File | Purpose |
|---|---|
| `.env` | All settings and tokens — **never commit** |
| `credentials.json` | Google OAuth credentials — **never commit** |
| `token.json` | Auto-generated Google access token — **never commit** |
| `~/.canvas_manager/habits.json` | Your schedule preferences |
| `.canvas_manager_deadlines.json` | Local deadline cache |

---

## Security

`.env`, `credentials.json`, and `token.json` are excluded from git by default. Never share or commit these files.
