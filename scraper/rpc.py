"""Read Aerodrome /vote data — exactly the way the frontend computes it.

Strategy:
  • Total Voting Power  ← VotingEscrow.totalSupply() (matches frontend "1.0B")
  • New Emissions       ← Minter tail-mode formula
                          (totalSupply × tailEmissionRate ÷ MAX_BPS when in tail)
  • Total Fees / Total Incentives  ← velodrome-finance/sugar-sdk
                          (uses Aerodrome's on-chain price oracle — same source
                           the frontend uses, so USD totals match exactly)

I tried hand-rolling the Sugar reads with raw ABIs (multiple iterations) and
got ~14× low on fees and ~3× low on incentives. The official SDK already
implements the right per-pool aggregation + the canonical pricing oracle, so
we delegate to it.

References:
  https://github.com/velodrome-finance/sugar-sdk
  https://github.com/aerodrome-finance/contracts/blob/main/contracts/Minter.sol
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import requests
from web3 import Web3


# ---- Aerodrome contract addresses on Base ----------------------------------
VOTER          = "0x16613524e02ad97eDfeF371bC883F2F5d6C480A5"
MINTER         = "0xeB018363F0a9Af8f91F06FEe6613a751b2A33FE5"
AERO_TOKEN     = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
VOTING_ESCROW  = "0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4"

# The same RPC Aerodrome's frontend uses.
DEFAULT_BASE_RPC = "https://lb.drpc.live/base/Avibgvi26EjPsw76UtdwmsQOcPkUJIUR8YARurWHF38a"
EPOCH_LENGTH = 7 * 24 * 60 * 60

# Minter constants from aerodrome-finance/contracts/Minter.sol
MAX_BPS = 10_000
TAIL_START = 8_969_150 * 10**18

# ---- ABIs (minimal — for the things sugar-sdk doesn't aggregate) ----------
VOTER_ABI = [
    {"inputs":[],"name":"totalWeight","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"length","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

VOTING_ESCROW_ABI = [
    {"inputs":[],"name":"totalSupply","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

MINTER_ABI = [
    {"inputs":[],"name":"weekly","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"tailEmissionRate","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"epochCount","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

ERC20_TOTAL_SUPPLY_ABI = [
    {"inputs":[],"name":"totalSupply","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]


# ---- Result dataclass ------------------------------------------------------
@dataclass
class VoteSnapshot:
    epoch_number: int
    epoch_starts_at: int
    total_voting_power: float
    new_emissions: float
    aero_price_usd: float
    emissions_value: float
    total_fees: float
    total_incentives: float
    total_rewards: float
    multiplier: float
    pool_count: int
    debug: dict = field(default_factory=dict)


# ---- Web3 helpers ----------------------------------------------------------
def _rpc_url() -> str:
    return os.environ.get("BASE_RPC_URL") or DEFAULT_BASE_RPC


def make_web3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError(f"Base RPC unreachable: {_rpc_url()}")
    return w3


def get_aero_price_usd() -> float:
    """AERO/USD from DefiLlama (used for emissions_value and as a sanity check
    against whatever the SDK's pricing oracle returns)."""
    resp = requests.get(
        f"https://coins.llama.fi/prices/current/base:{AERO_TOKEN.lower()}",
        timeout=15,
    )
    resp.raise_for_status()
    coins = resp.json().get("coins") or {}
    key = f"base:{AERO_TOKEN.lower()}"
    price = coins.get(key, {}).get("price")
    if price is None:
        raise RuntimeError(f"DefiLlama missing AERO price: {coins}")
    return float(price)


# ---- The main entry point --------------------------------------------------
def read_snapshot(w3: Optional[Web3] = None) -> VoteSnapshot:
    w3 = w3 or make_web3()

    # 1. Total Voting Power — VotingEscrow.totalSupply matches frontend exactly
    veaero = w3.eth.contract(address=Web3.to_checksum_address(VOTING_ESCROW), abi=VOTING_ESCROW_ABI)
    ve_total_supply = veaero.functions.totalSupply().call()

    # 2. New Emissions — Aerodrome's tail-mode formula
    minter = w3.eth.contract(address=Web3.to_checksum_address(MINTER), abi=MINTER_ABI)
    aero = w3.eth.contract(address=Web3.to_checksum_address(AERO_TOKEN), abi=ERC20_TOTAL_SUPPLY_ABI)
    weekly_state = minter.functions.weekly().call()
    aero_total_supply = aero.functions.totalSupply().call()
    tail_rate = minter.functions.tailEmissionRate().call()
    epoch_count = minter.functions.epochCount().call()
    in_tail = weekly_state < TAIL_START
    if in_tail:
        emission_gross = (aero_total_supply * tail_rate) // MAX_BPS
    else:
        emission_gross = weekly_state
    print(
        f"[rpc] ve.totalSupply={ve_total_supply/1e18:,.4f} "
        f"weekly_state={weekly_state/1e18:,.4f} in_tail={in_tail} "
        f"aero.totalSupply={aero_total_supply/1e18:,.4f} tailRate={tail_rate} "
        f"new_emissions={emission_gross/1e18:,.4f}",
        flush=True,
    )

    # 3. Total Fees + Total Incentives via sugar-sdk
    # The SDK uses Aerodrome's on-chain price oracle → same USD values the
    # frontend displays.
    #
    # - get_pools(): all 1700+ pools with per-pool current-epoch token0_fees +
    #   token1_fees in USD. Summed across pools = Total Fees (frontend match).
    # - get_latest_pool_epochs(): per-pool epoch data; only voted pools have
    #   active bribes, so sum total_incentives across these = Total Incentives.
    os.environ["SUGAR_RPC_URI_8453"] = _rpc_url()
    from sugar.chains import BaseChain  # imported here so import errors are loud
    with BaseChain() as chain:
        pools = chain.get_pools()
        epochs = chain.get_latest_pool_epochs()
    total_fees = sum(float(p.total_fees) for p in pools if p.total_fees)
    total_incentives = sum(float(ep.total_incentives) for ep in epochs)
    print(
        f"[rpc] sugar-sdk: {len(pools)} pools, {len(epochs)} pool-epochs · "
        f"fees=${total_fees:,.2f} incentives=${total_incentives:,.2f}",
        flush=True,
    )

    # 4. Aggregate
    aero_price = get_aero_price_usd()
    new_emissions = emission_gross / 1e18
    emissions_value = new_emissions * aero_price
    total_rewards = total_fees + total_incentives
    multiplier = emissions_value / total_rewards if total_rewards > 0 else 0.0

    # Voter contract still useful for pool count (and as a cross-check)
    voter = w3.eth.contract(address=Web3.to_checksum_address(VOTER), abi=VOTER_ABI)
    pool_count = voter.functions.length().call()

    # Latest epoch start = floor(now / WEEK) × WEEK in UTC (Thursday 00:00)
    latest_ts = max((ep.ts for ep in epochs), default=0) if epochs else 0
    epoch_number = (latest_ts // EPOCH_LENGTH) if latest_ts else 0

    return VoteSnapshot(
        epoch_number=epoch_number,
        epoch_starts_at=latest_ts,
        total_voting_power=ve_total_supply / 1e18,
        new_emissions=new_emissions,
        aero_price_usd=aero_price,
        emissions_value=emissions_value,
        total_fees=total_fees,
        total_incentives=total_incentives,
        total_rewards=total_rewards,
        multiplier=multiplier,
        pool_count=pool_count,
        debug={
            "weekly_state_raw": weekly_state,
            "aero_total_supply_raw": aero_total_supply,
            "tail_emission_rate": tail_rate,
            "epoch_count_minter": epoch_count,
            "in_tail": in_tail,
            "sugar_pool_epochs": len(epochs),
        },
    )
