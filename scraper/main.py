"""Entry point. Dispatched by GitHub Actions workflows.

Usage:
  python -m scraper.main --mode 8h
  python -m scraper.main --mode epoch
  python -m scraper.main --mode 8h --dry-run        # no DB write, no TG post
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from dotenv import load_dotenv

from scraper import notify, rpc, store
from scraper.multiplier import compute as compute_multiplier_metrics


def run_8h(dry_run: bool) -> int:
    print("[8h] reading Aerodrome state from Base RPC", flush=True)
    snap = rpc.read_snapshot()
    print(
        f"[8h] epoch={snap.epoch_number} starts_at={snap.epoch_starts_at} "
        f"VP={snap.total_voting_power:,.0f} new_em={snap.new_emissions:,.0f} "
        f"aero=${snap.aero_price_usd:.4f} fees=${snap.total_fees:,.0f} "
        f"incentives=${snap.total_incentives:,.0f} rewards=${snap.total_rewards:,.0f} "
        f"mult={snap.multiplier:.3f} pools={snap.pool_count}",
        flush=True,
    )

    # Run the standard simulation math against the same emissions_value / rewards.
    result = compute_multiplier_metrics(
        new_emissions=snap.new_emissions,
        aero_price_usd=snap.aero_price_usd,
        total_rewards=snap.total_rewards,
    )

    if dry_run:
        print("[8h] DRY RUN — skipping DB write and Telegram post")
        print(json.dumps({**asdict(snap), **result.to_dict()}, indent=2, default=str))
        return 0

    sb = store.client()
    snapshot_id = store.insert_snapshot_8h(
        sb,
        new_emissions=result.new_emissions,
        aero_price_usd=result.aero_price_usd,
        emissions_value=result.emissions_value,
        total_rewards=result.total_rewards,
        total_fees=snap.total_fees,
        total_incentives=snap.total_incentives,
        total_voting_power=snap.total_voting_power,
        multiplier=result.multiplier,
        sim_plus_1k=result.sim_plus_1k,
        sim_plus_25k=result.sim_plus_25k,
        sim_plus_50k=result.sim_plus_50k,
        sim_plus_100k=result.sim_plus_100k,
        raw={
            "epoch_number": snap.epoch_number,
            "epoch_starts_at": snap.epoch_starts_at,
            "pool_count": snap.pool_count,
            "debug": snap.debug,
        },
    )
    print(f"[8h] inserted snapshot id={snapshot_id}", flush=True)

    # Previous multiplier for delta display
    prev = (
        sb.table("snapshots_8h")
        .select("multiplier")
        .neq("id", snapshot_id)
        .order("captured_at", desc=True)
        .limit(1)
        .execute()
    )
    prev_mult = float(prev.data[0]["multiplier"]) if prev.data else None

    msg = notify.format_8h_message(
        epoch_number=snap.epoch_number,
        multiplier=result.multiplier,
        prev_multiplier=prev_mult,
        total_voting_power=snap.total_voting_power,
        total_fees=snap.total_fees,
        total_incentives=snap.total_incentives,
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
    """Per-epoch snapshot at Wed 23:00 UTC.

    Phase 1: capture the same numbers as the 8h snapshot, plus the winning
    pool (largest vote weight). Pool-by-pool vote weights are derived from the
    LpEpoch.votes field returned by RewardsSugar.epochsLatest().
    """
    from scraper.rpc import REWARDS_SUGAR, REWARDS_SUGAR_ABI, make_web3
    from web3 import Web3

    print("[epoch] reading Aerodrome state from Base RPC", flush=True)
    snap = rpc.read_snapshot()

    w3 = make_web3()
    sugar = w3.eth.contract(address=Web3.to_checksum_address(REWARDS_SUGAR), abi=REWARDS_SUGAR_ABI)

    # Re-fetch epochs to find the pool with the largest vote weight
    epochs = []
    BATCH = 100
    offset = 0
    safety = 0
    while safety < 50:
        safety += 1
        try:
            batch = sugar.functions.epochsLatest(BATCH, offset).call()
        except Exception:
            if BATCH > 20:
                BATCH = 20
                continue
            raise
        if not batch:
            break
        epochs.extend(batch)
        if len(batch) < BATCH:
            break
        offset += BATCH

    if not epochs:
        print("[epoch] no pool epoch data — aborting", file=sys.stderr)
        return 2

    # Pick the pool with the highest votes (LpEpoch.votes is index 2)
    winner_epoch = max(epochs, key=lambda e: e[2])
    winner_pool = winner_epoch[1]
    winner_votes_raw = winner_epoch[2]
    total_votes_raw = sum(e[2] for e in epochs) or 1
    winner_votes = winner_votes_raw / 1e18
    total_votes = total_votes_raw / 1e18
    pct = (winner_votes / total_votes) * 100.0

    print(
        f"[epoch] winner pool={winner_pool} votes={winner_votes:,.0f} "
        f"pct={pct:.2f}%",
        flush=True,
    )

    if dry_run:
        print("[epoch] DRY RUN — skipping DB write and Telegram post")
        print(json.dumps({
            "winner_pool": winner_pool,
            "winner_votes": winner_votes,
            "total_votes": total_votes,
            "pct": pct,
            **asdict(snap),
        }, indent=2, default=str))
        return 0

    sb = store.client()
    store.insert_epoch_winner(
        sb,
        epoch_number=snap.epoch_number,
        pair=winner_pool,                # we don't have a friendly symbol yet
        pool_address=winner_pool,
        votes=winner_votes,
        total_votes=total_votes,
        pct_of_total=pct,
        is_ignition=False,               # TODO: enrich with LpSugar.byAddress(pool).symbol
        raw={
            "epoch_starts_at": snap.epoch_starts_at,
            "pool_count": snap.pool_count,
        },
    )

    msg = notify.format_epoch_winner_message(
        epoch_number=snap.epoch_number,
        pair=winner_pool,
        votes=winner_votes,
        total_votes=total_votes,
        pct_of_total=pct,
        is_ignition=False,
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
