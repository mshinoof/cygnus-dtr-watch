# KSEB DTR capacity watch

Tracks transformer-wise renewable capacity published by KSEB at
<https://wss.kseb.in/selfservices/reCap> and tells you when something moves.

You get an alert when:

| What happened | Why you care |
|---|---|
| **Transformer upgraded** (90% capacity went up) | A 100 kVA became a 250 kVA. Tens of kW of headroom opened at once. This is the big one. |
| **New transformer listed** | Fresh capacity in a section, usually nobody's booked it yet. |
| **Capacity freed** (balance rose, capacity unchanged) | Someone's feasibility lapsed or a sanction was released. |
| **Capacity booked** (balance fell) | A competitor took it. Matters if you had a customer pending on that DTR. |
| **Capacity reduced / transformer delisted** | Usually a KSEB data correction — worth knowing *before* you promise a customer feasibility. |

---

## Setup

### 1. Install

```bash
cd kseb-dtr-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Probe the site first

This step matters. The KSEB page is a JSF/PrimeFaces app, and the scraper drives
a real browser rather than guessing at its AJAX protocol. Before the first real
run, confirm the page looks the way the scraper expects:

```bash
python -m tracker.run probe
```

A browser window opens and drives itself. When it finishes you get `probe.json`
containing every dropdown, its options, and every background request the page
fired. Two things to check:

- **The district and section names.** Copy the exact spellings into `config.yml`.
- **The captured XHR calls.** If one of them returns clean JSON, tell me and I'll
  rewrite the scraper to hit that endpoint directly. It would run in seconds
  instead of minutes and wouldn't need a browser at all.

### 3. Configure

Edit `config.yml`. Start with one district:

```yaml
targets:
  - district: KANNUR
    sections: "*"
```

### 4. First run

```bash
python -m tracker.run scrape
```

The first run only saves a baseline — there's nothing to compare against yet.
It reports changes from the second run onward.

Use `--dry` to scrape and diff without sending any alerts.

---

## Getting alerts

Telegram is the easiest and it's free:

1. Message **@BotFather**, send `/newbot`, follow the prompts. He hands you a token.
2. Make a group ("Cygnus DTR alerts"), add the bot to it.
3. Post any message in the group, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `chat.id`.
4. Export the two values:

```bash
export TELEGRAM_TOKEN="8123456789:AAF..."
export TELEGRAM_CHAT_ID="-1001234567890"
```

Email works too — set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`.
Both fire if both are configured; neither is required.

---

## Running it on a schedule

**On GitHub (recommended).** Push this to a repo, add the Telegram values under
Settings → Secrets and variables → Actions, and `.github/workflows/track.yml`
runs it at 06:30 IST daily. Each run commits the new snapshot, so `git log` on
`data/` becomes a permanent audit trail of how KSEB's numbers moved — genuinely
useful when a customer's feasibility gets questioned six months later.

Publish the repo to Pages and `dashboard.html` is your live view, in the same
place as your other Cygnus tools.

**On the MacBook.** `crontab -e`, then:

```
30 6 * * * cd ~/kseb-dtr-tracker && .venv/bin/python -m tracker.run scrape >> run.log 2>&1
```

The Mac has to be awake for cron to fire, which is why GitHub is the better home
for this.

---

## The dashboard

`dashboard.html` is regenerated on every run. Single file, data baked in, no
server and no internet needed — same pattern as your proposal and receipt tools.

The load meter next to each transformer is the thing to read: solid bar is
already grid-connected, hatched is feasibility issued but not yet commissioned,
empty is yours to sell. The copper tick shows where the balance line stood at the
previous check, so you can see movement at a glance.

---

## Notes on behaving well

The data is public and published under a regulatory transparency mandate, so
reading it is entirely legitimate. Two things keep it that way:

- **Scrape once a day, not once an hour.** KSEB updates this data on a slow
  cycle; polling harder gets you nothing but load on a public utility's server.
  `delay_seconds: 3` between sections is deliberate — leave it alone.
- **Start with the sections you actually work in.** All of Kerala is roughly
  75,000 transformers across 700-odd sections. A full-state scrape is a
  multi-hour job and mostly wasted.

A safety rail is built in: if a run returns fewer than 60% of the previous run's
rows, it aborts rather than saving. Without it, one failed page load would look
like hundreds of transformers vanishing and flood you with false alerts.

---

## When KSEB changes the page

They will, eventually. The symptom is a run that scrapes zero rows and refuses
to save. Run `python -m tracker.run probe` again with `headless: false` and watch
what the browser does. `tracker/scrape.py` is the only file that needs to change;
the diff engine, storage, alerts and dashboard don't care where the rows came from.

## Layout

```
tracker/scrape.py     browser automation, table parsing, name normalisation
tracker/diff.py       change detection and classification   (tested)
tracker/store.py      JSON snapshots + rolling change log
tracker/notify.py     Telegram / email
tracker/dashboard.py  renders the self-contained HTML
tracker/run.py        CLI: probe | scrape | render
tests/test_diff.py    python tests/test_diff.py
```
