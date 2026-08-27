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

`missed call` → `texted in seconds` → `reply logged` → `owner pinged` → `1 tap to close it out`

<br />

</div>

<br />

## ▍ The problem this exists to kill

A customer calls. Nobody picks up. They don't leave a voicemail — almost nobody does anymore — they just hang up and call the next business on the list.

That's the entire transaction. No error, no crash, nothing to debug. The lead was real, the intent was real, and it evaporated in the eleven seconds it took the phone to stop ringing. Multiply that by every missed call a small business gets during a job, a lunch break, or after hours, and it's not a minor inconvenience — it's one of the largest silent revenue leaks most service businesses have.

**This system's entire reason for existing is to close that window before it closes.**

<br />

## ▍ What it actually does

The moment a call goes unanswered, a text is already on its way to the caller before they've had time to put the phone down. If they text back, the owner doesn't have to go check anything — a fully-formed lead card lands in their Telegram with the caller's number, the time, and what they said, and **closing the loop out is a single tap.** Meanwhile, every lead is quietly building itself a row in a Google Sheet the owner already knows how to read, with zero manual data entry, ever.

Nobody has to open an app. Nobody has to remember to log anything. The business owner's entire experience of this system is: *phone rings, phone is missed, a card appears on Telegram moments later, tap a button when it's handled.*

<br />

## ▍ The full loop

<div align="center">

```
   📞 CUSTOMER CALLS THE BUSINESS NUMBER
              │
              ▼
   ┌──────────────────────────┐
   │   TWILIO VOICE WEBHOOK     │   looks up the dialed number against
   │      /webhook/voice        │   client_configs → forwards the call
   └──────────────────────────┘   to the owner's real cell phone
              │
              ▼
        rings for 20s...
              │
      ┌───────┴────────┐
      ▼                ▼
  ANSWERED          NO-ANSWER / BUSY / FAILED
  (call ends,       │
   nothing to do)   ▼
             ┌──────────────────────────┐
             │   /webhook/voice-status    │  →  lead written to MongoDB
             │   "missed call detected"   │      (status: NEW)
             └──────────────────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │   AUTO-REPLY SMS FIRED     │  →  fires in the background,
             │   via Twilio, in seconds   │      never blocks the webhook
             └──────────────────────────┘
                          │
              customer texts back
                          │
                          ▼
             ┌──────────────────────────┐
             │   /webhook/sms-inbound     │  →  matched to the open lead
             │   "reply received"         │      by phone number
             └──────────────────────────┘
                    │             │
                    ▼             ▼
         ┌────────────────┐  ┌──────────────────────┐
         │ GOOGLE SHEETS    │  │  TELEGRAM LEAD CARD   │
         │ row appended /   │  │  🚨 caller · time ·    │
         │ updated by       │  │  reply · 3 buttons:   │
         │ Call SID         │  │  📞 ✅ ❌              │
         └────────────────┘  └──────────────────────┘
                                        │
                                owner taps ONE button
                                        │
                                        ▼
                         ┌───────────────────────────────┐
                         │ MongoDB status updated  +       │
                         │ Google Sheet cell updated  +    │
                         │ Telegram message edits in-place │
                         │        all three, instantly     │
                         └───────────────────────────────┘
```

</div>

<br />

## ▍ Anatomy of a save

**T+0s** — A customer calls. Twilio hits `/webhook/voice`, the system matches the dialed number to a business in `client_configs`, and dials the owner's real phone. If the number isn't registered, the call is rejected outright — no orphaned calls, no ambiguity.

**T+20s** — Nobody picks up. Twilio calls back into `/webhook/voice-status` with the outcome. Anything that isn't a clean answer — `no-answer`, `busy`, `failed`, `canceled` — is treated as a missed call. A lead document is born in MongoDB with a full lifecycle of flags already primed: `initial_sms_sent`, `followup_sms_sent`, `customer_replied`, `owner_status`, `owner_reminder_sent`.

**T+21s** — Before that request has even finished, a `BackgroundTask` fires the auto-reply SMS through Twilio, templated per-business (`"{business_name}"` swapped in live) so every client's bot sounds like *their* business, not a generic script.

**T+ a few minutes** — The customer texts back. `/webhook/sms-inbound` finds their most recent open lead by phone number, stamps `customer_replied: true` and the reply text into Mongo, and kicks off two background jobs in parallel:

- a thread-safe `gspread` write that either appends a new row or **updates the existing one in place**, keyed by `Call SID` so a single lead never becomes duplicate rows no matter how many times it changes state;
- a rich Telegram card to the owner — caller number, missed time, and the customer's exact reply, with three inline buttons baked right into the message.

**T+ one tap** — The owner taps **📞 Contacted**, **✅ Booked**, or **❌ Lost**. That single tap updates the MongoDB record, rewrites the status cell in the Sheet, *and* edits the original Telegram message in place to show the new status — three systems, one motion, zero context-switching.

<br />

## ▍ Design decisions worth recording

<table>
<tr><td width="30%"><b>Multi-tenant from day one</b></td><td>Every Twilio number, forwarding phone, SMS template, Sheet ID, and Telegram chat ID lives in a per-client <code>client_configs</code> document. The same deployment can run an unlimited number of businesses side-by-side without touching code.</td></tr>
<tr><td><b>Webhooks never wait on the slow part</b></td><td>SMS sends, Sheets writes, and Telegram pushes are <i>all</i> dispatched via <code>BackgroundTasks</code> (and <code>asyncio.to_thread</code> for the blocking <code>gspread</code> calls). Twilio always gets an instant, empty <code>&lt;Response/&gt;</code> — no webhook ever risks a timeout retry storm.</td></tr>
<tr><td><b>Call SID as the anchor, everywhere</b></td><td>MongoDB, Google Sheets, and Telegram's callback payloads are all keyed off the same Twilio Call SID. It's the one identifier that's guaranteed unique and present at every stage, so nothing ever gets double-logged or cross-wired between leads.</td></tr>
<tr><td><b>Idempotent sheet writes</b></td><td><code>log_new_lead_reply</code> reads column F before writing — if the Call SID already has a row, it updates in place instead of appending. The sheet a business owner opens is always the current truth, never a growing pile of duplicates.</td></tr>
<tr><td><b>One-tap resolution, not a dashboard</b></td><td>No login, no app, no separate CRM UI for the owner to check. The entire "close this lead out" action is three buttons under a message that was already pushed to their phone.</td></tr>
<tr><td><b>Reject unknown numbers outright</b></td><td>If a call lands on a Twilio number with no matching (or inactive) <code>client_config</code>, the call is rejected at the TwiML layer before any logic runs — a clean, deliberate failure mode instead of a silent one.</td></tr>
</table>

<br />

## ▍ The data model

<table>
<tr><td width="50%" valign="top">

**`leads`** — one document per missed call

```
client_id             → which business
caller_phone           → who called
twilio_number           → which line they dialed
call_sid                → the anchor key, everywhere
call_time                → when it rang
call_status               → no-answer / busy / failed
initial_sms_sent           → auto-reply fired?
customer_replied            → did they text back?
reply_text                    → what they said
owner_status                    → NEW / CONTACTED / BOOKED / LOST
owner_reminder_sent               → nudge the owner? (in progress)
```

</td><td width="50%" valign="top">

**`client_configs`** — one document per business

```
twilio_number            → their public number
owner_forwarding_phone     → their real cell
owner_telegram_chat_id       → where alerts land
business_name                  → for the SMS template
initial_sms_template             → their custom auto-reply
google_sheet_id                    → their live lead log
active                                → kill switch, per client
```

</td></tr>
</table>

<br />

## ▍ How this actually got built

No sprint planning, no ticket board — just a straight, sequential build, one working system stacked on the last. The commit history *is* the changelog:

<table>
<tr><td width="16%"><code>a54b650</code></td><td>Folder structure, skeleton, day one.</td></tr>
<tr><td><code>ae85dac</code></td><td>MongoDB wired up and tested before a single webhook existed.</td></tr>
<tr><td><code>6a31411</code></td><td>Twilio credentials in place — the system can finally text a human.</td></tr>
<tr><td><code>fac997f</code></td><td>Inbound reply capture — now it can <i>listen</i>, not just talk.</td></tr>
<tr><td><code>2b83e32</code></td><td>Google Sheets connected — every reply becomes a durable row.</td></tr>
<tr><td><code>2628dbb</code></td><td>Telegram alerts added — the owner stops needing to check anything.</td></tr>
<tr><td><code>4326d17</code></td><td><b>"Completed the loop, all the apps integrated and tested."</b></td></tr>
</table>

<br />

## ▍ What's still in the oven

Being transparent about the state of things, for the record: the **follow-up scheduler is scaffolded but not yet wired up.** `tester.py` already defines the exact two behaviors it's built for and seeds mock data to prove them out —

- **Task A:** a lead who never replied to the auto-text gets a gentle follow-up SMS after a couple of hours of silence.
- **Task B:** a lead who *did* reply, but whose status the owner never updated, triggers a reminder nudge instead of quietly going stale.

`APScheduler` is already a dependency and `app/scheduler/jobs.py` is staged for exactly this — it's the next thing that gets built, not a hidden gap.

<br />

## ▍ Stack

<div align="center">

| Layer | Technology |
|:--|:--|
| **Voice & SMS** | `Twilio` — call forwarding, missed-call detection, two-way SMS |
| **Server** | `FastAPI` + `uvicorn` — async webhooks, lifespan-managed background bot |
| **Owner interface** | `python-telegram-bot` — rich cards, inline one-tap status buttons |
| **Live CRM** | `gspread` + Google Sheets API — per-client, idempotent, human-readable |
| **Persistence** | `MongoDB` via `motor` (fully async) — leads + multi-tenant client configs |
| **Scheduling (upcoming)** | `APScheduler` — timed follow-ups and owner reminders |

</div>

<br />

## ▍ Philosophy

The best automation is the one the business owner never has to think about. They don't learn a dashboard. They don't check an inbox. Their phone rings, they miss it, and moments later a lead is already being worked — by a system, until a single tap says it's theirs to finish.

<br />

---

<div align="center">

<sub>Want a system like this deployed for your own business? <a href="https://vectorworkflows.com"><b>Contact Vector Workflows →</b></a></sub>

<br />

<sub>Crafted by <a href="https://vectorworkflows.com"><b>Vector Workflows</b></a></sub>

</div>
