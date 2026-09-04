# Deploy & Runbook

Written for the actual setup: Hetzner + Caddy + **MongoDB Atlas** (no local
mongo container any more) + a Twilio number whose SMS is not yet approved.

---

## What changed and why

| Change | Reason |
|---|---|
| `docker-compose.yml` no longer sets `MONGO_URI` | It was overriding `.env`, so the app read an empty local database while `manage_clients.py` wrote to Atlas. That one line caused every symptom. |
| The local `mongo` service is **deleted** | It published 27017 on `0.0.0.0` with no password. A bot found it and wiped the databases. Your data is in Atlas; there is nothing left to secure. |
| App port bound to `127.0.0.1:8005` | Caddy proxies to it. It never needs to be internet-facing. |
| `sms_enabled` per client | The IVR stops promising a text it can't send. |
| `Dockerfile` (capital D) | Lowercase `dockerfile` fails to build on Linux. |

---

## Step 1 — Deploy

On Windows, in your **`Missed Lead Responder`** repo (the one with `.git`):

```bash
git rm --cached dockerfile        # drop the lowercase one from git
git add -A
git commit -m "v2: Atlas-only, resilience layer, honest IVR when SMS unavailable"
git push
```

On Hetzner:

```bash
cd /root/Missed-Lead-Responder
cp .env .env.backup-$(date +%F)
rm -f dockerfile                  # remove the leftover lowercase file
git pull
```

Add the new keys to `.env` (see `.env.example` — your existing `MONGO_URI`,
`TWILIO_*`, `TELEGRAM_BOT_TOKEN` and `GOOGLE_SERVICE_ACCOUNT_JSON` stay as they are):

```bash
TWILIO_MESSAGING_SERVICE_SID=MGe6c28f40d5e697597b3e480092d6f34e
OPS_TELEGRAM_CHAT_ID=7670860583

FALLBACK_ENABLED=true
FALLBACK_CLIENT_ID=vector_workflows
FALLBACK_BUSINESS_NAME=Vector Workflows
FALLBACK_TWILIO_NUMBER=+14704706323
FALLBACK_BOOKING_URL=https://cal.com/vectorworkflows/meeting-for-caller
FALLBACK_INTAKE_URL=https://tally.so/r/0QRgZN
FALLBACK_WEBSITE_URL=https://vectorworkflows.com
FALLBACK_TELEGRAM_CHAT_ID=7670860583
FALLBACK_SHEET_ID=1Gr7dpsX2MF-zmdYLVNnBnYsDUYc1sbLRrKCuSWwQ4Cw
FALLBACK_TIMEZONE=Asia/Kolkata
FALLBACK_SMS_ENABLED=false
```

Then:

```bash
docker compose down                 # removes the old mongo container too
docker volume rm lead_responder_persistent_db    # the ransomwared volume. optional but clean.
docker compose up -d --build
docker compose logs -f app
```

Healthy boot:

```
Mongo target    : db=missed_call_responder hosts=[...mongodb.net] uri=mongodb+srv://<credentials-hidden>@...
MongoDB connection OK
Client cache loaded: 1 active client(s) from database.
Outbox ready at /data/outbox.db
BOOT OK - 1 client(s) live from database.
```

---

## Step 2 — Fix the "Apex Plumbing" greeting

Your Atlas `client_configs` has a document whose `twilio_number` is your number
but whose `business_name` is a test client. See what's there:

```bash
docker compose exec app python manage_clients.py list
```

Then either update it, or add the right one (same command re-run with an
existing ID updates it in place):

```bash
docker compose exec app python manage_clients.py add
```

Answer `n` to **"Can this number SEND SMS?"** until A2P/toll-free is approved.
Disable the test client so it can't win the number lookup:

```bash
docker compose exec app python manage_clients.py disable apex_plumbing
```

Changes are picked up within 60 seconds. No restart, no deploy.

---

## Step 3 — Twilio console

Phone Numbers → +1 470 470 6323 → Configure:

| Field | Value |
|---|---|
| A call comes in (POST) | `https://lead.vectorworkflows.com/webhook/voice` ✓ already set |
| **Primary handler fails** | `https://lead.vectorworkflows.com/webhook/voice` ← **empty, set it** |
| **Call status changes (POST)** | `https://lead.vectorworkflows.com/webhook/call-status` ← **empty, set it** |
| A message comes in (POST) | `https://lead.vectorworkflows.com/webhook/sms-inbound` ✓ already set |

**Call status changes is the important one.** It's what turns "rang, heard two
seconds, hung up" into a lead. Without it those callers stay invisible.

---

## Step 4 — Verify

```bash
docker compose exec app python diagnose.py
```

Then call your number. You should hear:

> "Thanks for calling Vector Workflows! Press 1 to leave a brief voicemail about
> what you need, and we'll call you back. Press 2 to request a callback, and
> we'll get straight back to you today. Or visit vectorworkflows dot com to book
> a time instantly on our calendar."

No mention of a text. Press 2 → Telegram card + Sheet row. Then **hang up during
the greeting on a second call** — you should get a "MISSED LEAD (caller hung up)"
card. That case produced nothing at all before.

---

## When SMS is finally approved

One command, no deploy:

```bash
docker compose exec app python manage_clients.py add   # same client id, answer y to SMS
```

The 1/2/3 menu with texted links comes back immediately.

---

## Daily life

| Event | What reaches you |
|---|---|
| Every morning 9am IST | `✅ Daily check: system healthy` + 24h counts |
| Boot in a bad state | `🚨 Started in a degraded state` |
| Atlas unreachable | `⚠️ Running on fallback config` (calls still work) |
| Queue backing up | `⚠️ Outbox backing up` |
| A job gives up | `❌ Job permanently failed` |
| SMS undelivered | `⚠️ SMS not delivered` + Twilio error code |

**If the 9am heartbeat doesn't arrive, something is wrong.** That's its whole
purpose — it makes silence meaningful.

```bash
docker compose logs -f app                              # live logs
curl -s https://lead.vectorworkflows.com/health/deep | jq
docker compose exec app python diagnose.py              # full self-test
docker compose exec app python manage_clients.py leads  # recent leads
```

---

## Atlas security checklist (do once)

1. **Project → Access Manager → Users** — confirm every listed user is you.
   Remove anything you don't recognise.
2. **Network Access** — if `0.0.0.0/0` is allowlisted, replace it with your
   Hetzner IP `178.105.237.108/32`.
3. **Database Access** — one database user, strong password. Rotate it and
   update `MONGO_URI` if you're unsure who's seen it.
4. **Backup** — on the free tier there are no automatic backups. Weekly dump:

```bash
0 3 * * 0 docker exec lead_responder_app python -c "
import asyncio,json,datetime
from app.database import leads_collection, client_configs_collection
async def m():
    out={'leads':await leads_collection.find({}).to_list(10000),
         'clients':await client_configs_collection.find({}).to_list(500)}
    print(json.dumps(out,default=str))
asyncio.run(m())" > ~/backups/leads-$(date +\%F).json
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "This number is currently unavailable" | No client config **and** no fallback | Fill the `FALLBACK_*` block |
| Wrong business name in greeting | Another client doc owns that number | `manage_clients.py list`, then `disable` the wrong one |
| All calls 403 | `PUBLIC_BASE_URL` or proxy headers wrong | Logs print every URL the signature check tried |
| No Telegram alerts | Bot never messaged from your account | Message the bot, then `diagnose.py` |
| `NetworkError: Bad Gateway` in logs | Telegram API hiccup | Self-recovers now; only worry if constant |
| SMS error 30034 | A2P not registered | Keep `sms_enabled=false` until approved |
