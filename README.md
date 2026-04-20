# Canvas Manager

Automatically fetches your Canvas assignments and Google Calendar events, then sends you daily email and SMS reminders — no Twilio required. Uses the Gmail API to send both.

---

## Features

- Fetches upcoming assignments directly from Canvas
- Fetches events from Google Calendar automatically (no manual `.ics` import needed)
- Sends formatted HTML email reminders with due dates and submission status
- Sends SMS reminders via your carrier's email-to-SMS gateway
- Shows `✓ submitted` indicator for already-submitted Canvas assignments
- Daily cron job syncs and sends reminders automatically every morning
- Interactive `setup` command — no manual file editing required
- `habits` command — saves your wake/sleep times, focus block length, and hard-stop ranges
- `plan` command — auto-generates a study schedule for the day based on your habits and deadlines
- `schedule` subcommands — manually add, move, update, delete, or clear blocks on any day
- Export any day's schedule to a `.ics` file with `plan --export`

---

## Requirements

- Python 3.10+
- A Canvas account with API access
- A Gmail account (used to send email and SMS)
- A Google Cloud project with **Gmail API** and **Google Calendar API** enabled
- A US phone number and your carrier name

---

## Installation

```bash
pip install .
```

This installs the `canvas-manager` command.

---

## Setup

### Step 1 — Get your Canvas API token

1. Log in to Canvas (e.g. `https://canvas.youruniversity.edu`)
2. Go to **Account → Settings**
3. Scroll to **Approved Integrations → New Access Token**
4. Give it a name, click **Generate Token**, and copy it — you only see it once

---

### Step 2 — Set up Google Cloud credentials

This allows the app to send emails/SMS via Gmail and read your Google Calendar.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Go to **APIs & Services → Library** and enable:
   - **Gmail API**
   - **Google Calendar API**
4. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**, fill in the app name and your email
   - Add your Gmail address as a test user
5. Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Choose **Desktop app**
   - Click **Download JSON**
6. Rename the downloaded file to exactly `credentials.json` and place it in the project directory

> **Note:** Google names the downloaded file with a long ID (e.g. `client_secret_....json`). It must be renamed to `credentials.json` or the app will not find it.

> The first time you run `sync`, a browser window opens for you to authorize the app. A `token.json` file is created automatically and reused. **Do not commit either file to git.**

---

### Step 3 — Run the interactive setup

```bash
canvas-manager setup
```

This walks you through all configuration interactively — no manual file editing needed:

```
────────── Canvas Manager — Setup ──────────

Canvas
  Canvas base URL [https://canvas.cmu.edu]:
  Canvas API token:
  Verifying Canvas credentials... OK

Email
  Send reminders to (email):

SMS
  Your phone number (e.g. +11234567890):
  Your carrier (tmobile/att/verizon/...):

Google Calendar
  Google Calendar ID [primary]:

Reminder settings
  Daily reminder time (HH:MM, 24h) [08:00]:
  Days ahead to include in reminders [3]:

✓ Saved config to .env
  Install daily cron job? [Y/n]: Y
✓ Cron job set for 08:00 daily
```

The setup command:
- Validates your Canvas URL and token with a live API call
- Validates your Google Calendar ID against the Calendar API
- Writes all settings to `.env`
- Installs the daily cron job automatically

---

### Step 4 — Fetch your first deadlines

```bash
canvas-manager sync
```

Fetches upcoming assignments from Canvas and events from Google Calendar, merges them, and saves locally.

---

### Step 5 — Preview your reminder

```bash
canvas-manager remind --preview
```

Shows what the email and SMS will look like without sending anything.

---

## Commands

### `setup`
Interactive first-time configuration. Fills `.env` and installs the cron job.

```bash
canvas-manager setup
```

---

### `sync`
Fetch the latest assignments from Canvas and events from Google Calendar.

```bash
canvas-manager sync

# Skip Google Calendar and fetch Canvas only
canvas-manager sync --no-gcal
```

---

### `list`
Show upcoming deadlines from the local cache.

```bash
canvas-manager list
canvas-manager list --days 7
```

Defaults to 14 days ahead. Shows source (`canvas` or `gcal`) and submission status.

---

### `remind`
Send email and/or SMS reminders.

```bash
# Send both email and SMS
canvas-manager remind

# Preview without sending
canvas-manager remind --preview

# Email only
canvas-manager remind --email-only

# SMS only
canvas-manager remind --sms-only

# Override lookahead window
canvas-manager remind --days 5

# Send to a different email address
canvas-manager remind --to-email someone@example.com

# Append today's schedule to the email
canvas-manager remind --schedule

# Append a specific day's schedule
canvas-manager remind --schedule --schedule-date 2026-05-01
```

---

### `habits`
Set up your daily schedule preferences. Saved to `~/.canvas_manager/habits.json` and used by `plan`.

```bash
canvas-manager habits
```

Prompts for:
- Wake and sleep times
- Peak focus hours (e.g. `09:00-11:00, 14:00-16:00`)
- Preferred focus block length (minutes)
- Break length between blocks (minutes)
- Hard-stop ranges (e.g. lunch, commute — blocks are never placed here)

---

### `plan`
Generate and view the study plan for a day. Reads your habits and the deadlines cache.

```bash
# Plan today
canvas-manager plan

# Plan a specific date
canvas-manager plan --date 2026-05-01

# Clear existing auto-generated blocks and replan from scratch
canvas-manager plan --overwrite

# Export the day's schedule to a .ics file
canvas-manager plan --export

# Export to a specific path
canvas-manager plan --export --out ~/Desktop/today.ics
```

**How it works:**
1. Places GCal timed events (classes, meetings) at their exact time slots.
2. Places study blocks for Canvas assignments due that day in remaining free time, sorted by urgency.
3. Respects hard-stop ranges and never schedules blocks in the past (when planning today).

---

### `schedule`
Manually adjust blocks on any day. These changes persist alongside auto-generated blocks.

```bash
# Add a block (overlapping blocks are allowed)
canvas-manager schedule add "Office Hours" --from 14:00 --to 15:00 --type other
canvas-manager schedule add "Study: 18-665" --from 10:00 --to 11:30 --type study --date 2026-05-01

# Move a block (provide one or both bounds; duration is preserved when only one is given)
canvas-manager schedule move blk_a1b2c3d4 --from 11:00
canvas-manager schedule move blk_a1b2c3d4 --from 11:00 --to 12:30

# Update a block's title or type
canvas-manager schedule update blk_a1b2c3d4 --title "Revised title"
canvas-manager schedule update blk_a1b2c3d4 --type break

# Delete a single block
canvas-manager schedule delete blk_a1b2c3d4

# Clear all blocks for a day
canvas-manager schedule clear
canvas-manager schedule clear --date 2026-05-01 --yes
```

Block types: `class`, `assignment`, `study`, `break`, `personal`, `other`.

---

### `clear-cache`
Delete the local deadlines cache (`.canvas_manager_deadlines.json`). Run `sync` to rebuild.

```bash
canvas-manager clear-cache
```

---

### `setup-cron`
Print the crontab line for daily reminders (already installed by `setup`, use this to change the time).

```bash
canvas-manager setup-cron
canvas-manager setup-cron --time 09:30
```

Add the printed line to your crontab with `crontab -e`.

---

### `import-ical`
Manually import a `.ics` calendar file and merge it with Canvas (optional — `sync` handles this automatically via Google Calendar).

```bash
canvas-manager import-ical ~/Downloads/calendar.ics
canvas-manager import-ical   # prompts for the file path
```

---

## How the daily cron works

Once set up, every morning at your chosen time the cron runs:

```
canvas-manager sync && canvas-manager remind
```

1. `sync` — fetches fresh assignments from Canvas + Google Calendar
2. `remind` — sends the email and SMS with everything due in the next N days

Logs are written to `~/.canvas_manager.log`.

---

## Google Calendar ID

By default the app uses your primary Google Calendar. To use a different calendar:

1. Open [Google Calendar](https://calendar.google.com)
2. Click the three dots next to the calendar → **Settings**
3. Scroll to **Calendar ID** (looks like `abc123@group.calendar.google.com`)
4. Enter it when prompted during `setup`, or update `GCAL_CALENDAR_ID` in `.env`

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
| `.env` | All your settings and tokens — **never commit this** |
| `credentials.json` | Google OAuth credentials from Cloud Console — **never commit this** |
| `token.json` | Auto-generated Google access token — **never commit this** |
| `.canvas_manager_deadlines.json` | Local cache of deadlines — safe to gitignore |
| `~/.canvas_manager/habits.json` | Your habits profile (wake/sleep, focus blocks, hard-stops) |
| `~/.canvas_manager/plans/YYYY-MM-DD.json` | Per-day schedule blocks |

---

## Security

The following files contain sensitive credentials and are excluded from git by default:

```
.env
credentials.json
token.json
```

Never share or commit these files.
