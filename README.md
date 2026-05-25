# aero-tracker

Scheduled scraper for [Aerodrome Finance](https://aerodrome.finance/vote) on Base. Computes the AERO emissions multiplier, snapshots the per-epoch vote winner, persists to Supabase, and pushes updates to a Telegram channel.

Companion dashboard: [aero-tracker-web](https://github.com/Agentic-Po/aero-tracker-web).

## What it does

Two scheduled jobs, both running on GitHub Actions:

| Job | Schedule (UTC) | What it captures |
|---|---|---|
| `snapshot-8h` | `5 */8 * * *` (every 8h at :05) | Total voting power, total fees, total incentives, total rewards, new emission, AERO price, computed multiplier, and simulated multipliers at +$1k / +$25k / +$50k / +$100k incentives |
| `snapshot-epoch` | `0 23 * * 3` (Wednesdays 23:00 UTC, 1h before epoch flip) | The pool with the largest vote weight: pair, vote count, % of total votes, and whether it's the Aerodrome Ignition pool |

All numbers are pulled from the Aerodrome frontend (via [Lightpanda](https://lightpanda.io/) — the public `/vote` page is JS-rendered and Cloudflare-protected). AERO price comes from CoinGecko.

## Multiplier formula

```
emissions_value = new_emissions × AERO_price_usd
multiplier      = emissions_value ÷ total_rewards
```

The dashboard warns when the multiplier drops below **1.1**.

## Stack

- **Compute / cron:** GitHub Actions (free, public, no personal hardware)
- **Scrape:** Lightpanda CLI (headless browser, ~10x lighter than Chromium)
- **Storage:** Supabase Postgres (single source of truth)
- **Push:** Telegram Bot API → [@devAerodromeM](https://t.me/devAerodromeM) (dev), [@AeroEmissionMultiplier](https://t.me/AeroEmissionMultiplier) (prod)

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env  # then fill in values
python -m scraper.main --mode 8h --dry-run
```

`--dry-run` skips DB writes and Telegram posts; it prints the parsed values to stdout.

## Deploy

1. Fork or clone this repo
2. Create a Supabase project, run `sql/migrations/0001_init.sql` in the SQL editor
3. Set GitHub Actions secrets (see `.env.example` for the full list)
4. The workflows run automatically; `workflow_dispatch` lets you trigger manually for testing

## License

MIT
