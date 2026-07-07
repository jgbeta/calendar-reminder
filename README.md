# Google Calendar Reminders Slack Bot

Get Slack notifications before your meetings — 30 minutes and 5 minutes before each event, plus instant alerts when a near-term meeting is cancelled or rescheduled. Designed to run as a lightweight Docker container on any always-on machine (home server, NAS, VPS), with SQLite-backed reminder state that survives restarts. Supports Google Meet, Zoom, Microsoft Teams, or any event with a URL in the location or description field.

```
Event starting in 5m.
Weekly sync with manager
By: manager@company.com
Monday, June 01, 2026 at 02:55 PM
https://meet.google.com/xxx-yyy-zzz
```

---

## Before you start

You'll need:
- A **Google account** whose calendar you want to monitor
- A **Slack workspace** where you have permission to add apps
- **Docker** (recommended for always-on use) OR **Python 3.10+** for running locally
- **Git** to clone this repository

Total setup time: approximately 20 minutes.

---

## Part 1 — Google Cloud setup

The bot reads your calendar through the official Google Calendar API using your own OAuth credentials. This keeps your calendar data private — it never passes through any third-party service.

### 1.1 Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **Select a project** (top bar) → **New Project**
3. Name it (e.g. `calendar-slack-bot`) and click **Create**
4. Make sure the new project is selected in the top bar

### 1.2 Enable the Google Calendar API

1. In the left menu go to **APIs & Services → Library**
2. Search for **Google Calendar API**
3. Click it → **Enable**

### 1.3 Configure the OAuth consent screen

This step is required before you can create credentials.

1. Go to **APIs & Services → OAuth consent screen**
2. User type: **External** → **Create**
3. Fill in:
   - **App name**: anything (e.g. `Calendar Slack Bot`)
   - **User support email**: your Gmail address
   - **Developer contact email**: your Gmail address
4. Click **Save and Continue** through the remaining screens
5. On the **Test users** screen, click **Add Users** and enter your Gmail address
6. Click **Save and Continue**, then **Back to Dashboard**

> The "Test users" step is required because the app is in testing mode. Only the email addresses you add here can authenticate.

### 1.4 Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name it anything → **Create**
5. Click **Download JSON** on the confirmation screen
6. Rename the downloaded file to `credentials.json` and place it in the project root directory

---

## Part 2 — Slack bot setup

### 2.1 Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App → From scratch**
3. Name it (e.g. `Calendar Bot`) and select your workspace → **Create App**

### 2.2 Add permissions

1. In the left sidebar click **OAuth & Permissions**
2. Scroll to **Scopes → Bot Token Scopes**
3. Click **Add an OAuth Scope** and add:
   - `chat:write` — allows the bot to post messages

### 2.3 Install to workspace

1. Scroll to the top of **OAuth & Permissions**
2. Click **Install to Workspace → Allow**
3. Copy the **Bot User OAuth Token** — it starts with `xoxb-`
4. This value goes in `SLACK_BOT_TOKEN` in your `.env` file

### 2.4 Find the channel ID

The bot posts to a specific channel. To find its ID:

1. Open Slack in a **web browser** (not the desktop app)
2. Navigate to the channel you want notifications in
3. The URL looks like `https://app.slack.com/client/TXXXXXXXX/CXXXXXXXXX`
4. The last segment starting with `C` is the channel ID → this is `SLACK_DM_CHANNEL_ID`

Then invite the bot to that channel:
```
/invite @your-bot-name
```

> You can also use a Direct Message channel. Open the DM in a browser and extract the ID from the URL (it starts with `D`).

### 2.5 Find your Slack user ID

The bot can @-mention you in every notification so you get a push notification on your phone.

1. Open Slack and click your **profile photo** (bottom-left)
2. Click **Profile**
3. Click the **⋮** (three dots) menu → **Copy member ID**
4. It starts with `U` → this goes in `SLACK_MENTION_USER_ID`

---

## Part 3 — Configure the bot

### 3.1 Clone the repository

```bash
git clone https://github.com/jgbeta/calendar-reminder.git
cd calendar-bot
```

Place `credentials.json` (from Part 1) in this directory.

### 3.2 Install dependencies

```bash
pip install -r requirements.txt
```

### 3.3 Generate your Google token (one-time, requires a browser)

This step exchanges your OAuth credentials for a long-lived token that the bot can use without opening a browser at runtime.

```bash
mkdir -p data

python3 scripts/bootstrap_google_token.py \
  --credentials credentials.json \
  --token data/token.json
```

A browser window opens. Log in with the Google account from Step 1.3 (the one you added as a test user). After approving, the browser closes and `data/token.json` is written.

> Run this script once on a machine with a browser. After that, the bot refreshes the token automatically and you never need to repeat this step — unless you revoke access or the token is deleted.

### 3.4 Configure `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
HEADLESS=true
GOOGLE_CREDENTIALS_PATH=/run/secrets/google_credentials.json
GOOGLE_TOKEN_PATH=/data/token.json
GOOGLE_SYNC_STATE_PATH=/data/google-sync-state.json
CALENDAR_BOT_DB_PATH=/data/calendar-bot.sqlite
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_DM_CHANNEL_ID=C09CPUB59B4
SLACK_MENTION_USER_ID=U079MLMM1CK
IGNORED_CREATOR_EMAILS=your.email@company.com,noreply@company.com
CALENDAR_ID=primary
POLL_SECONDS=60
NOTIFICATION_HORIZON_DAYS=7
SLACK_RATE_LIMIT_WARNING_COOLDOWN_SECONDS=900
CALENDAR_BOT_UID=1000
CALENDAR_BOT_GID=1000
```

| Variable | What it is | Where to get it |
|---|---|---|
| `SLACK_BOT_TOKEN` | Bot OAuth token | Slack app -> OAuth & Permissions (Step 2.3) |
| `SLACK_DM_CHANNEL_ID` | Channel to post messages to | Slack web URL (Step 2.4) |
| `SLACK_MENTION_USER_ID` | Slack user to @-mention | Your Slack profile -> Copy member ID (Step 2.5) |
| `IGNORED_CREATOR_EMAILS` | Events created by these emails are silently skipped | Your own email; calendar bots; noreply addresses |
| `GOOGLE_CREDENTIALS_PATH` | Path to the OAuth secrets file in Docker | `/run/secrets/google_credentials.json` from `docker-compose.yml` |
| `GOOGLE_TOKEN_PATH` | Path for the generated token | `/data/token.json` in Docker |
| `GOOGLE_SYNC_STATE_PATH` | Legacy JSON state import path | `/data/google-sync-state.json` if upgrading from the old JSON cache |
| `CALENDAR_BOT_DB_PATH` | SQLite event/reminder state cache | `/data/calendar-bot.sqlite` |
| `CALENDAR_ID` | Which calendar to monitor | `primary` for your main Google calendar |
| `POLL_SECONDS` | How often to check for changes | `60` (once per minute) |
| `NOTIFICATION_HORIZON_DAYS` | How far ahead to send cancellation, reschedule, and reminder notifications | `7` |
| `SLACK_RATE_LIMIT_WARNING_COOLDOWN_SECONDS` | How long to wait before sending one rate-limit warning after Slack throttles the bot | `900` (15 minutes) |
| `CALENDAR_BOT_UID` / `CALENDAR_BOT_GID` | Container user used for the `/data` bind mount | `1000` works for most Linux desktop users |
| `HEADLESS` | Prevents the bot from opening a browser | `true` for Docker; `false` only for first-time local setup |

> **`IGNORED_CREATOR_EMAILS`**: Add your own email here. Google Calendar often records events you created yourself as having your email as the creator, and you usually do not need reminders for meetings you set up.
>
> For local, non-Docker runs, use paths like `GOOGLE_CREDENTIALS_PATH=credentials.json`, `GOOGLE_TOKEN_PATH=data/token.json`, and `CALENDAR_BOT_DB_PATH=data/calendar-bot.sqlite`.

---

## Part 4 — Run the bot

### Option A — Docker (recommended)

Docker keeps the bot running continuously and restarts it automatically if it crashes.

```bash
docker compose up -d --build
```

Check that it started correctly:
```bash
docker compose logs -f
```

You should see:
```
INFO calendar_slack_bot.sync Calendar sync returned N events; next_sync_token present
```

To stop:
```bash
docker compose down
```

### Option B — Run locally

```bash
calendar-slack-bot
```

Press `Ctrl-C` to stop. The bot loads `.env` automatically on startup.

### Updating the bot after making changes

| What changed | Command |
|---|---|
| `.env` only | `docker compose up` (no rebuild needed) |
| Any `.py` file, `pyproject.toml`, or `requirements.txt` | `docker compose up --build` |
| Just restart | `docker compose restart calendar-slack-bot` |

---

## What the messages look like

**Upcoming meeting reminder (sent at 30m and 5m before start):**
```
@YourName
Event starting in 30m.
Weekly sync with manager
By: manager@company.com
Monday, June 01, 2026 at 03:00 PM
https://zoom.us/j/123456789
```

**Meeting rescheduled:**
```
@YourName
Event updated, pushed back by 30 min.
Weekly sync with manager
By: manager@company.com
Monday, June 01, 2026 at 03:30 PM
https://zoom.us/j/123456789
```

**Meeting cancelled:**
```
@YourName
Event Deleted.
Weekly sync with manager
By: manager@company.com
Monday, June 01, 2026 at 03:00 PM
```

The bot skips:
- All-day events (no specific start time)
- Events created by addresses in `IGNORED_CREATOR_EMAILS`
- Events that have already started
- Cancellation, reschedule, and reminder notifications for events beyond `NOTIFICATION_HORIZON_DAYS`

---

## Troubleshooting

**`RuntimeError: Missing or invalid Google OAuth token at data/token.json`**
The token file is missing or expired. Re-run the bootstrap script:
```bash
python3 scripts/bootstrap_google_token.py --credentials credentials.json --token data/token.json
```

**`Sync token expired. Clearing only the token; cached events/reminders remain in SQLite.`**
This is normal when Google expires a sync token after inactivity. The bot keeps cached future reminders in SQLite and runs a full sync on the next poll. If the warning repeats every minute indefinitely, stop the container, move `data/calendar-bot.sqlite` aside, and restart to rebuild the cache.

**`Failed to post Slack message`**
Usually a bad or expired `SLACK_BOT_TOKEN`. Verify the token in your Slack app settings (OAuth & Permissions). Also check that the bot was invited to the channel with `/invite @your-bot-name`.

**`Slack rate-limited calendar bot notifications; suppressing the current burst`**
Slack rejected a burst of messages. The bot stops sending normal notifications for the current burst, records the number suppressed in SQLite, and later sends one warning after `SLACK_RATE_LIMIT_WARNING_COOLDOWN_SECONDS`. It does not retry the suppressed post.

**Meeting has a Zoom / Teams link but message shows "(no meeting link)"**
The event must have a `conferenceData` entry, or a URL somewhere in the `location` or `description` field. The bot checks all three automatically. If the link is embedded in an HTML anchor tag in the description, it will still be found.

**Bot sends notifications for events I don't care about**
Add the event organizer's email to `IGNORED_CREATOR_EMAILS` in `.env`, then restart the container.

---

## Project structure

```
src/calendar_slack_bot/
├── main.py              Entry point — polling loop, notification logic
├── auth.py              Headless-safe Google OAuth token loading
├── calendar_events.py   Normalizes raw Google Calendar events (Meet, Zoom, Teams, etc.)
├── config.py            Environment variable config loader
├── healthcheck.py       Docker healthcheck for the SQLite heartbeat
├── message_rendering.py Formats Slack notification text
├── slack_client.py      Slack Web API wrapper
├── state_store.py       SQLite event/reminder/sync state cache
├── sync.py              Google Calendar full/incremental sync client
└── timer_manager.py     Legacy in-memory timer utility covered by tests

scripts/
├── bootstrap_google_token.py   One-time OAuth flow (needs a browser)
└── convert_pickle_token.py     Converts old token.pickle format to token.json

tests/                   Unit tests (no credentials required)
data/                    Runtime data directory (token.json, sync state)
```

---

## Security notes

- `credentials.json` and `data/token.json` contain sensitive credentials. Never commit them to git. They are listed in `.gitignore`.
- `data/calendar-bot.sqlite` contains cached calendar event metadata, meeting links, and reminder status. Treat it as private runtime data.
- The bot requests read-only access to your calendar (`calendar.readonly` scope). It cannot create, modify, or delete events.
- The Slack bot token (`xoxb-...`) has write access to the channel you specify. Keep it secret.
