<div align="center">

<br />

<pre>
  ███╗   ███╗██╗███████╗███████╗███████╗██████╗       ██╗     ███████╗ █████╗ ██████╗
  ████╗ ████║██║██╔════╝██╔════╝██╔════╝██╔══██╗      ██║     ██╔════╝██╔══██╗██╔══██╗
  ██╔████╔██║██║███████╗███████╗█████╗  ██║  ██║      ██║     █████╗  ███████║██║  ██║
  ██║╚██╔╝██║██║╚════██║╚════██║██╔══╝  ██║  ██║      ██║     ██╔══╝  ██╔══██║██║  ██║
  ██║ ╚═╝ ██║██║███████║███████║███████╗██████╔╝      ███████╗███████╗██║  ██║██████╔╝
  ╚═╝     ╚═╝╚═╝╚══════╝╚══════╝╚══════╝╚═════╝       ╚══════╝╚══════╝╚═╝  ╚═╝╚═════╝
</pre>

### THE MISSED-LEAD RESPONDER

**A ringing phone nobody answers is a closed door. This turns it back into an open one — automatically, in the time it takes the caller to lock their screen.**

<sub>by <a href="https://vectorworkflows.com"><b>VECTOR WORKFLOWS</b></a> — precision-engineered automation</sub>

<br />

[![Python](https://img.shields.io/badge/PYTHON-3.11+-000000?style=for-the-badge&logo=python&logoColor=00D9FF)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FASTAPI-Async_Core-000000?style=for-the-badge&logo=fastapi&logoColor=00D9FF)](https://fastapi.tiangolo.com/)
[![Twilio](https://img.shields.io/badge/TWILIO-Voice_%26_SMS-000000?style=for-the-badge&logo=twilio&logoColor=00D9FF)](https://www.twilio.com/)
[![Telegram](https://img.shields.io/badge/TELEGRAM-Owner_Alerts-000000?style=for-the-badge&logo=telegram&logoColor=00D9FF)](https://core.telegram.org/bots)
[![Google Sheets](https://img.shields.io/badge/GOOGLE_SHEETS-Live_CRM-000000?style=for-the-badge&logo=googlesheets&logoColor=00D9FF)](https://developers.google.com/sheets/api)
[![MongoDB](https://img.shields.io/badge/MONGODB-Motor_Async-000000?style=for-the-badge&logo=mongodb&logoColor=00D9FF)](https://www.mongodb.com/)

<br />

`call answered` → `menu, or hang up` → `texted in seconds` → `logged` → `owner pinged` → `1 tap to close it out`

<br />

</div>

<br />

## ▍ The problem this exists to kill

A customer calls. Nobody picks up. They don't leave a voicemail — almost nobody does anymore — they hang up and call the next business on the list.

That's the entire transaction. No error, no crash, nothing to debug. The lead was real, the intent was real, and it evaporated in the eleven seconds it took the phone to stop ringing.

**This system's reason for existing is to close that window before it closes.**

<br />

## ▍ What it actually does

Every call is answered instantly by an IVR that greets the caller by business name and offers three options. Whatever they do next — press a key, leave a voicemail, or hang up two seconds in — a lead exists, a text goes out, a Telegram card lands on the owner's phone, and a row appears in their Google Sheet.

Closing the loop is one tap.

<br />

## ▍ The full loop

<div align="center">

```
   📞 CUSTOMER CALLS THE BUSINESS NUMBER
              │
              ▼
   ┌────────────────────────────┐
   │   /webhook/voice            │  client config resolved from an in-memory
   │   answers immediately       │  cache — never a live DB call on this path
   │   LEAD CREATED RIGHT HERE   │
   └────────────────────────────┘
              │
              ▼
      IVR menu is spoken
              │
    ┌─────────┼─────────┬──────────────┐
    ▼         ▼         ▼              ▼
  PRESS 1   PRESS 2   PRESS 3      HANGS UP / stays on line
  voicemail  intake   booking      │
    │         link      link       ▼
    │         │         │     ┌──────────────────────┐
    │         │         │     │ /webhook/call-status  │
    │         │         │     │ "abandoned" → LEAD    │
    │         │         │     └──────────────────────┘
    └─────────┴────┬────┴──────────────┘
                   ▼
        ┌──────────────────────────┐
        │   DURABLE OUTBOX (SQLite) │  every side effect is written to disk
        │   retries w/ backoff      │  BEFORE it is attempted
        └──────────────────────────┘
             │        │         │
             ▼        ▼         ▼
        ┌────────┐ ┌────────┐ ┌──────────────┐
        │  SMS   │ │ SHEETS │ │ TELEGRAM CARD │
        │ Twilio │ │  row   │ │ 📞 ✅ ❌       │
        └────────┘ └────────┘ └──────────────┘
                                     │
                            owner taps ONE button
                                     ▼
                    ┌────────────────────────────────┐
                    │ MongoDB + Google Sheet + the     │
                    │ Telegram message itself, all     │
                    │ updated in one motion            │
                    └────────────────────────────────┘

        customer texts back at any point
                   │
                   ▼
        ┌──────────────────────────┐
        │  /webhook/sms-inbound     │  matched to a lead within 14 days,
        │  (or creates a new lead)  │  or logged as a fresh SMS-only lead
        └──────────────────────────┘
```

</div>

<br />

## ▍ Built so it cannot quietly fail

This is the part that matters more than the features. Every one of these exists because the naive version of it broke in production.

<table>
<tr><td width="32%"><b>The IVR never depends on a live database</b></td><td>Client config resolves through four tiers: in-memory cache → live Mongo read → on-disk JSON snapshot → environment-variable fallback. The database can be entirely gone and the caller still hears a correct, branded menu with working links. There is no infrastructure failure that produces <i>"this number is currently unavailable."</i></td></tr>
<tr><td><b>Durable outbox, not fire-and-forget</b></td><td>Every SMS, Telegram card, Sheets write and lead record is written to a local SQLite queue <i>first</i>, then dispatched by a background worker with exponential backoff out to ~24 hours. Google can 429 you, Telegram can go down, Mongo can restart — nothing is lost, it just drains late. The queue lives on a mounted volume, so a redeploy doesn't drop work mid-retry.</td></tr>
<tr><td><b>The lead is created before the greeting finishes</b></td><td>Not after the caller navigates the menu. Someone who hangs up three seconds in is the most common real-world case and the most valuable one to catch — a <code>statusCallback</code> converts that abandonment into a lead with an SMS and an alert.</td></tr>
<tr><td><b>Idempotency keys on every job</b></td><td>Voicemail fires two Twilio webhooks for the same recording. Retries re-run jobs. Every queued item carries a unique key, so the owner gets exactly one card per event — never a duplicate, never a missing one.</td></tr>
<tr><td><b>HTML-escaped Telegram output</b></td><td>Customer text goes through <code>html.escape</code> before it reaches Telegram. Legacy Markdown mode rejects any message containing <code>_</code>, <code>*</code> or <code>[</code> — meaning a perfectly ordinary customer text silently destroyed the alert.</td></tr>
<tr><td><b>Webhooks never block on slow work</b></td><td>Twilio gets its TwiML in milliseconds. Blocking calls (<code>gspread</code>, Twilio's REST client) run in threads, never on the event loop.</td></tr>
<tr><td><b>Errors degrade into speech, not silence</b></td><td>An unhandled exception on a voice route returns a polite spoken message instead of Twilio's <i>"an application error has occurred"</i> — and pings the operator on Telegram at the same time.</td></tr>
<tr><td><b>Self-monitoring with a daily heartbeat</b></td><td>A watchdog reports queue backlogs, dead-lettered jobs, degraded config and zero-client states. A 9am heartbeat reports the last 24 hours. Silence becomes meaningful: no heartbeat means something is wrong.</td></tr>
<tr><td><b>One source of truth for config</b></td><td><code>MONGO_URI</code> is set in exactly one place. The original compose file set it in both <code>env_file</code> and <code>environment</code>, and Compose's precedence rules meant the app silently used a different database than the onboarding script — the single defect behind every symptom this system ever had.</td></tr>
</table>

<br />

## ▍ The data model

<table>
<tr><td width="50%" valign="top">

**`leads`** — one document per call or cold text

```
client_id              → which business
caller_phone            → who called
twilio_number            → which line they dialled
call_sid                  → the anchor key, everywhere
call_time                  → when it rang
call_status                 → RINGING / IVR_COMPLETED /
                               VOICEMAIL_RECEIVED /
                               ABANDONED / SMS_ONLY
ivr_selection                 → 1, 2, 3, none
initial_sms_sent               → auto-reply fired?
followup_sms_sent               → nudge sent?
customer_replied                 → did they text back?
reply_text                        → latest message
messages[]                         → full conversation
recording_url                       → voicemail audio
owner_status                         → NEW / CONTACTED /
                                        BOOKED / LOST
owner_reminder_sent                   → nudged the owner?
alerted                                → card delivered?
```

</td><td width="50%" valign="top">

**`client_configs`** — one document per business

```
_id                     → client slug
business_name            → spoken in the greeting
twilio_number             → their public number
owner_telegram_chat_id     → where alerts land
google_sheet_id             → their live lead log
booking_url                  → sent on "3"
intake_form_url               → sent on "2"
followup_sms_template          → the 2-hour nudge
timezone                        → per-client local time
active                           → kill switch
```

Unique index on `call_sid`; compound indexes for the
inbound-SMS lookup and both scheduler queries.

</td></tr>
</table>

<br />

## ▍ Scheduled jobs

| Job | Cadence | What it does |
|:--|:--|:--|
| **Follow-up SMS** | every 10 min | Texts a lead who never replied after 2 hours of silence. Once only. |
| **Owner reminder** | every 15 min | Nudges the owner about a replied lead still sitting on `NEW` after 4 hours. |
| **Watchdog** | every 5 min | Reports queue backlog, dead letters, degraded config, zero clients. |
| **Heartbeat** | daily 9am | `✅ System healthy` with 24-hour lead, reply and booking counts. |

<br />

## ▍ Stack

<div align="center">

| Layer | Technology |
|:--|:--|
| **Voice & SMS** | `Twilio` — IVR, DTMF capture, voicemail, two-way SMS, delivery receipts |
| **Server** | `FastAPI` + `uvicorn` — async webhooks, lifespan-managed bot and workers |
| **Owner interface** | `python-telegram-bot` — rich HTML cards, inline one-tap status buttons |
| **Live CRM** | `gspread` + Google Sheets API — per-client, idempotent, human-readable |
| **Persistence** | `MongoDB` via `motor` — leads + multi-tenant client configs |
| **Reliability** | `SQLite` outbox — durable retry queue for every side effect |
| **Scheduling** | `APScheduler` — follow-ups, reminders, watchdog, heartbeat |

</div>

<br />

## ▍ Running it

```bash
cp .env.example .env          # fill it in, including the FALLBACK_* block
docker compose up -d --build
docker compose exec app python diagnose.py        # full self-test
docker compose exec app python manage_clients.py add
```

Full instructions, Twilio console settings and the operational runbook are in
**[DEPLOY.md](DEPLOY.md)**. Resilience tests: `python tests/test_resilience.py`.

<br />

## ▍ Philosophy

The best automation is the one the business owner never has to think about. They don't learn a dashboard. They don't check an inbox. Their phone rings, they miss it, and moments later a lead is already being worked — by a system, until a single tap says it's theirs to finish.

Which means the system has to be trustworthy enough to stop watching. That is what most of the engineering above is actually for.

<br />

---

<div align="center">

<sub>Want a system like this deployed for your own business? <a href="https://vectorworkflows.com"><b>Contact Vector Workflows →</b></a></sub>

<br />

<sub>Crafted by <a href="https://vectorworkflows.com"><b>Vector Workflows</b></a></sub>

</div>
