"""Entry point. Dispatched by GitHub Actions workflows.

Usage:
  python -m scraper.main --mode 8h
  python -m scraper.main --mode epoch
  python -m scraper.main --mode 8h --dry-run        # print, don't write or notify
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from dotenv import load_dotenv

from scraper import lightpanda, notify, parse, price, store
from scraper.multiplier import compute as compute_multiplier

VOTE_URL = "https://aerodrome.finance/vote"

# Heuristic: pairs containing these tokens are considered "Ignition" pools.
# Aerodrome periodically promotes new pools as Ignition; refine this list as needed.
IGNITION_HINTS = {"IGN", "IGNITION"}


def _is_ignition(pair: str) -> bool:
    p = pair.upper()
    return any(hint in p for hint in IGNITION_HINTS)


def run_8h(dry_run: bool) -> int:
    print(f"[8h] fetching {VOTE_URL}", flush=True)
    html = lightpanda.fetch(VOTE_URL)
    page = parse.parse_summary(html)
    print(f"[8h] parsed summary: {page}", flush=True)

    aero_price = price.get_aero_usd()
    print(f"[8h] AERO price: ${aero_price}", flush=True)

    result = compute_multiplier(
        new_emissions=page.new_emissions,
        aero_price_usd=aero_price,
        total_rewards=page.total_rewards,
    )
    print(f"[8h] multiplier: {result.multiplier:.4f}", flush=True)

    if dry_run:
        print("[8h] DRY RUN — skipping DB write and Telegram post")
        print(json.dumps({"summary": page.__dict__ | {"pools": []}, **result.to_dict()}, indent=2, default=str))
        return 0

    sb = store.client()
    snapshot_id = store.insert_snapshot_8h(
        sb,
        new_emissions=result.new_emissions,
        aero_price_usd=result.aero_price_usd,
        emissions_value=result.emissions_value,
        total_rewards=result.total_rewards,
        total_fees=page.total_fees,
        total_incentives=page.total_incentives,
        total_voting_power=page.total_voting_power,
        multiplier=result.multiplier,
        sim_plus_1k=result.sim_plus_1k,
        sim_plus_25k=result.sim_plus_25k,
        sim_plus_50k=result.sim_plus_50k,
        sim_plus_100k=result.sim_plus_100k,
        raw={"page": page.__dict__ | {"pools": []}},
    )
    print(f"[8h] inserted snapshot id={snapshot_id}", flush=True)

    prev = (
        sb.table("snapshots_8h")
        .select("multiplier")
        .neq("id", snapshot_id)
        .order("captured_at", desc=True)
        .limit(1)
        .execute()
    )
    prev_mult = prev.data[0]["multiplier"] if prev.data else None

    msg = notify.format_8h_message(
        multiplier=result.multiplier,
        prev_multiplier=float(prev_mult) if prev_mult is not None else None,
        total_voting_power=page.total_voting_power,
        total_fees=page.total_fees,
        total_incentives=page.total_incentives,
        total_rewards=result.total_rewards,
        new_emissions=result.new_emissions,
        aero_price_usd=result.aero_price_usd,
        sim_plus_1k=result.sim_plus_1k,
        sim_plus_25k=result.sim_plus_25k,
        sim_plus_50k=result.sim_plus_50k,
        sim_plus_100k=result.sim_plus_100k,
    )
    notify.send(msg)
    print("[8h] telegram posted", flush=True)
    return 0


def run_epoch(dry_run: bool) -> int:
    print(f"[epoch] fetching {VOTE_URL}", flush=True)
    html = lightpanda.fetch(VOTE_URL)
    page = parse.parse_full(html)

    if not page.pools:
        print("[epoch] no pools parsed — aborting", file=sys.stderr)
        return 2

    winner = max(page.pools, key=lambda p: p.votes)
    total_votes = page.total_voting_power or sum(p.votes for p in page.pools)
    pct = (winner.votes / total_votes) * 100 if total_votes else 0.0
    is_ign = _is_ignition(winner.pair)

    print(
        f"[epoch] winner: {winner.pair} votes={winner.votes:,.0f} "
        f"pct={pct:.2f}% ignition={is_ign}",
        flush=True,
    )

    if dry_run:
        print("[epoch] DRY RUN — skipping DB write and Telegram post")
        print(json.dumps({"winner": asdict(winner), "total_votes": total_votes, "pct": pct, "is_ignition": is_ign}, indent=2))
        return 0

    sb = store.client()
    store.insert_epoch_winner(
        sb,
        epoch_number=None,
        pair=winner.pair,
        pool_address=winner.pool_address,
        votes=winner.votes,
        total_votes=total_votes,
        pct_of_total=pct,
        is_ignition=is_ign,
        raw={"all_pools": [asdict(p) for p in page.pools]},
    )

    msg = notify.format_epoch_winner_message(
        epoch_number=None,
        pair=winner.pair,
        votes=winner.votes,
        total_votes=total_votes,
        pct_of_total=pct,
        is_ignition=is_ign,
    )
    notify.send(msg)
    print("[epoch] telegram posted", flush=True)
    return 0


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["8h", "epoch"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.mode == "8h":
        return run_8h(args.dry_run)
    return run_epoch(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
