# aero-tracker

Reads [Aerodrome Finance](https://aerodrome.finance/vote) vote state directly from Base mainnet RPC, computes the AERO emissions multiplier, persists to Supabase, and pushes updates to a Telegram channel.

Companion dashboard: [aero-tracker-web](https://github.com/Agentic-Po/aero-tracker-web).

## Why RPC and not scraping

The Aerodrome `/vote` page is a JS-rendered SPA gated by a wallet-connect overlay in v5. Headless scraping (Lightpanda, Playwright + stubs) hits the gate; Reown AppKit explicitly disables the injected provider path, so window.ethereum stubs are ignored.

But we don't need to scrape — Aerodrome's frontend has **no off-chain API**. Every numeric value on `/vote` is computed client-side from on-chain reads via DRPC against the Voter, Minter, and Sugar contracts. By hitting the same RPC URL with the same contract calls, we get bit-identical numbers to what a connected-wallet user sees in the browser.

## What it does

Two scheduled jobs on GitHub Actions, both reading Base RPC directly:

| Job | Schedule (UTC) | What it captures |
|---|---|---|
| `snapshot-8h` | `5 */8 * * *` | Total voting power, total fees, total incentives, total rewards, new emissions, AERO price, multiplier, and +$1k/$25k/$50k/$100k simulations |
| `snapshot-epoch` | `0 23 * * 3` (Wed 23:00 UTC) | The pool with the largest vote weight: pair, vote count, % of total votes |

## Stack

- **Cron:** GitHub Actions (no personal hardware)
- **Data:** Base mainnet RPC (default: the same `lb.drpc.live/base` endpoint Aerodrome's frontend uses, overridable via `BASE_RPC_URL` secret)
- **Read contracts (Base):**
  - Voter: `0x16613524e02ad97eDfeF371bC883F2F5d6C480A5`
  - Minter: `0xeB018363F0a9Af8f91F06FEe6613a751b2A33FE5`
  - LpSugar: `0x69dD9db6d8f8E7d83887A704f447b1a584b599A1`
  - RewardsSugar: `0x1b121EfDaF4ABb8785a315C51D29BCE0552A7678`
- **Prices:** CoinGecko (`/simple/token_price/base` for reward tokens, `/simple/price` for AERO)
- **Storage:** Supabase Postgres
- **Push:** Telegram Bot API → [@devAerodromeM](https://t.me/devAerodromeM) (dev) → [@AeroEmissionMultiplier](https://t.me/AeroEmissionMultiplier) (prod when stable)

## Multiplier formula

```
emissions_value = new_emissions × AERO_price
multiplier      = emissions_value ÷ total_rewards
```

Where `total_rewards = total_fees + total_incentives`, both in USD, summed across all pools using the current epoch's `LpEpoch` data from `RewardsSugar.epochsLatest()`.

The dashboard warns when the multiplier drops below **1.1**.

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in values
python -m scraper.main --mode 8h --dry-run
```

`--dry-run` prints the snapshot JSON without writing to DB or posting to Telegram.

## License

MIT
