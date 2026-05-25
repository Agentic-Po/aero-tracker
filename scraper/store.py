"""Supabase writes via supabase-py (service_role key, server-side only)."""

from __future__ import annotations

import os
from typing import Optional

from supabase import Client, create_client


def client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def insert_snapshot_8h(
    sb: Client,
    *,
    new_emissions: float,
    aero_price_usd: float,
    emissions_value: float,
    total_rewards: float,
    total_fees: Optional[float],
    total_incentives: Optional[float],
    total_voting_power: Optional[float],
    multiplier: float,
    sim_plus_1k: float,
    sim_plus_25k: float,
    sim_plus_50k: float,
    sim_plus_100k: float,
    raw: Optional[dict] = None,
) -> int:
    row = {
        "new_emissions": new_emissions,
        "aero_price_usd": aero_price_usd,
        "emissions_value": emissions_value,
        "total_rewards": total_rewards,
        "total_fees": total_fees,
        "total_incentives": total_incentives,
        "total_voting_power": total_voting_power,
        "multiplier": multiplier,
        "sim_plus_1k": sim_plus_1k,
        "sim_plus_25k": sim_plus_25k,
        "sim_plus_50k": sim_plus_50k,
        "sim_plus_100k": sim_plus_100k,
        "raw": raw,
    }
    resp = sb.table("snapshots_8h").insert(row).execute()
    return resp.data[0]["id"]


def insert_pools(sb: Client, snapshot_id: int, pools: list[dict]) -> None:
    if not pools:
        return
    rows = [{"snapshot_id": snapshot_id, **p} for p in pools]
    sb.table("snapshots_pools").insert(rows).execute()


def insert_epoch_winner(
    sb: Client,
    *,
    epoch_number: Optional[int],
    pair: str,
    pool_address: Optional[str],
    votes: float,
    total_votes: float,
    pct_of_total: float,
    is_ignition: bool = False,
    raw: Optional[dict] = None,
) -> int:
    row = {
        "epoch_number": epoch_number,
        "pair": pair,
        "pool_address": pool_address,
        "votes": votes,
        "total_votes": total_votes,
        "pct_of_total": pct_of_total,
        "is_ignition": is_ignition,
        "raw": raw,
    }
    resp = sb.table("epoch_winners").insert(row).execute()
    return resp.data[0]["id"]
