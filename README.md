# Canvas Manager V3

Fetches Canvas assignments and iCal deadlines, then sends you email and SMS reminders — no Twilio required. Uses the Gmail API to send both.

---

## Requirements

- Python 3.10+
- A Canvas account with API access
- A Gmail account (used to send emails and SMS)
- A US phone number and your carrier name

---

## Installation

```bash
pip install .
```

This installs the `canvas-manager-v3` command globally.

---

## Setup

### Step 1 — Get your Canvas API token

1. Log in to your Canvas account (e.g. `https://canvas.youruniversity.edu`)
2. Go to **Account → Settings**
3. Scroll down to **Approved Integrations**
4. Click **New Access Token**
5. Give it a name, set an expiry if you want, then click **Generate Token**
6. Copy the token — you will only see it once

### Step 2 — Get your Google OAuth credentials

This lets the app send email and SMS through your Gmail account.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Go to **APIs & Services → Library** and enable the **Gmail API**
4. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**, fill in the app name and your email
   - Add your Gmail address as a test user
5. Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Choose **Desktop app**
   - Click **Download JSON**
6. Rename the downloaded file to `credentials.json` and place it in the directory where you run the command

> The first time you run `remind`, a browser window will open asking you to authorize the app. After that, a `token_v3.json` file is created automatically and reused. **Do not commit either file to git.**

### Step 3 — Create your `.env` file

In the same directory where you run the command, create a file named `.env`:

```env
# Canvas
CANVAS_BASE_URL=https://canvas.youruniversity.edu
CANVAS_API_TOKEN=your_canvas_token_here

# Email recipient
TO_EMAIL_ADDRESS=you@example.com
FROM_NAME=Canvas Manager

# SMS recipient
TO_PHONE_NUMBER=+11234567890
PHONE_CARRIER=tmobile

# Reminder settings
REMINDER_LOOKAHEAD_DAYS=3
REMINDER_TIME=08:00
```

#### Field reference

| Field | What to put here |
|---|---|
| `CANVAS_BASE_URL` | Your school's Canvas URL (no trailing slash) |
| `CANVAS_API_TOKEN` | The token you copied from Canvas settings |
| `TO_EMAIL_ADDRESS` | The email address to send reminders to |
| `FROM_NAME` | Display name shown on sent emails (optional, default: `Canvas Manager`) |
| `TO_PHONE_NUMBER` | Your 10-digit US phone number, with or without `+1` |
| `PHONE_CARRIER` | Your carrier — see supported carriers below |
| `REMINDER_LOOKAHEAD_DAYS` | How many days ahead to look for deadlines (default: `3`) |
| `REMINDER_TIME` | Used by the cron command to set the send time, in `HH:MM` 24h format |

#### Supported carriers for SMS

| Value to use | Carrier |
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

## Usage

### Fetch assignments from Canvas

```bash
canvas-manager-v3 sync
```

Pulls all upcoming assignments from Canvas and saves them locally.

### Import an iCal file

```bash
canvas-manager-v3 import-ical ~/Downloads/calendar.ics
```

Or run without arguments and paste/drag the file path when prompted:

```bash
canvas-manager-v3 import-ical
```

Merges the iCal events with your Canvas assignments and saves them locally.

### List upcoming deadlines

```bash
canvas-manager-v3 list
canvas-manager-v3 list --days 7
```

Shows deadlines from the local cache. Defaults to 14 days ahead.

### Send reminders

```bash
# Send both email and SMS
canvas-manager-v3 remind

# Preview without sending
canvas-manager-v3 remind --preview

# Email only
canvas-manager-v3 remind --email-only

# SMS only
canvas-manager-v3 remind --sms-only

# Override how many days ahead to include
canvas-manager-v3 remind --days 5

# Send to a different email address
canvas-manager-v3 remind --to-email someone@example.com
```

### Set up daily automatic reminders (cron)

```bash
canvas-manager-v3 setup-cron
```

Prints a crontab line you can paste into `crontab -e`. Uses the `REMINDER_TIME` from your `.env` by default, or override it:

```bash
canvas-manager-v3 setup-cron --time 09:00
```

---

## File reference

| File | Purpose |
|---|---|
| `.env` | Your tokens, phone number, email, and settings — **never commit this** |
| `credentials.json` | Google OAuth app credentials downloaded from Google Cloud — **never commit this** |
| `token_v3.json` | Auto-generated Gmail access token — **never commit this** |
| `.canvas_manager_v3_deadlines.json` | Local cache of your deadlines — safe to ignore in git |

---

## Security note

Your `.env` and `credentials.json` contain sensitive credentials. Make sure they are listed in your `.gitignore`:

```
.env
credentials.json
token_v3.json
```
