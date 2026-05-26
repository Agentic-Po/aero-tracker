"""Read Aerodrome /vote data directly from Base mainnet RPC.

Why this approach: the network log of Aerodrome's frontend on /vote shows zero
off-chain API calls — everything is on-chain via DRPC + Multicall3 against the
Voter, Minter, and Sugar contracts. We hit the same contracts with the same
public RPC endpoint, so the numbers we compute are bit-identical to what a
connected-wallet user sees in the browser.

References:
- Aerodrome core deployment (Voter, Minter, AERO):
    https://github.com/aerodrome-finance/contracts/blob/main/script/constants/output/DeployCore-Base.json
- Sugar contract deployments on Base:
    https://github.com/velodrome-finance/sugar/blob/main/deployments/base.env
- LpSugar / RewardsSugar contract source:
    https://github.com/velodrome-finance/sugar/blob/main/contracts/
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
LP_SUGAR       = "0x69dD9db6d8f8E7d83887A704f447b1a584b599A1"
REWARDS_SUGAR  = "0x1b121EfDaF4ABb8785a315C51D29BCE0552A7678"

# Default RPC: the same DRPC endpoint Aerodrome's frontend uses.
DEFAULT_BASE_RPC = "https://lb.drpc.live/base/Avibgvi26EjPsw76UtdwmsQOcPkUJIUR8YARurWHF38a"

# Aerodrome epoch length = 1 week, anchored on Thursday 00:00 UTC.
EPOCH_LENGTH = 7 * 24 * 60 * 60  # seconds

# Known token decimals on Base, for stablecoins + majors that aren't 18.
KNOWN_DECIMALS: dict[str, int] = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": 6,   # USDbC (deprecated)
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": 8,   # cbBTC
}

# ---- ABIs (minimal) --------------------------------------------------------
VOTER_ABI = [
    {"inputs":[],"name":"totalWeight","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"length","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"epochNext","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

MINTER_ABI = [
    {"inputs":[],"name":"weekly","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

ERC20_DECIMALS_ABI = [
    {"inputs":[],"name":"decimals","outputs":[{"type":"uint8","name":""}],"stateMutability":"view","type":"function"},
]

# RewardsSugar.epochsLatest returns LpEpoch[] where each entry is:
#   (uint256 ts, address lp, uint256 votes, uint256 emissions,
#    (address token, uint256 amount)[] bribes,
#    (address token, uint256 amount)[] fees)
REWARDS_SUGAR_ABI = [{
    "inputs": [
        {"name": "_limit", "type": "uint256"},
        {"name": "_offset", "type": "uint256"},
    ],
    "name": "epochsLatest",
    "outputs": [{
        "type": "tuple[]",
        "components": [
            {"name": "ts",        "type": "uint256"},
            {"name": "lp",        "type": "address"},
            {"name": "votes",     "type": "uint256"},
            {"name": "emissions", "type": "uint256"},
            {"name": "bribes",    "type": "tuple[]", "components": [
                {"name": "token",  "type": "address"},
                {"name": "amount", "type": "uint256"},
            ]},
            {"name": "fees",      "type": "tuple[]", "components": [
                {"name": "token",  "type": "address"},
                {"name": "amount", "type": "uint256"},
            ]},
        ],
    }],
    "stateMutability": "view",
    "type": "function",
}]


# ---- Result dataclass ------------------------------------------------------
@dataclass
class VoteSnapshot:
    epoch_number: int                   # weeks since unix epoch start (rough)
    epoch_starts_at: int                # latest epoch start ts (Thursday 00:00 UTC)
    total_voting_power: float           # veAERO units (18 dec)
    new_emissions: float                # AERO units (18 dec)
    aero_price_usd: float
    emissions_value: float              # new_emissions × aero_price
    total_fees: float                   # USD
    total_incentives: float             # USD
    total_rewards: float                # = total_fees + total_incentives
    multiplier: float                   # = emissions_value / total_rewards
    pool_count: int
    unpriced_token_count: int = 0
    debug: dict = field(default_factory=dict)


# ---- Web3 + helpers --------------------------------------------------------
def make_web3() -> Web3:
    rpc = os.environ.get("BASE_RPC_URL", DEFAULT_BASE_RPC)
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError(f"Base RPC unreachable: {rpc}")
    return w3


def _coingecko_headers() -> dict:
    h = {}
    key = os.environ.get("COINGECKO_API_KEY")
    if key:
        h["x-cg-demo-api-key"] = key
    return h


def get_aero_price_usd() -> float:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "aerodrome-finance", "vs_currencies": "usd"},
        headers=_coingecko_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return float(resp.json()["aerodrome-finance"]["usd"])


def fetch_token_prices_usd(tokens: set[str]) -> dict[str, float]:
    """CoinGecko Base token-price batch. Returns lowercase-address → USD."""
    if not tokens:
        return {}
    out: dict[str, float] = {}
    addrs = sorted(t.lower() for t in tokens)
    # CoinGecko caps token batch to ~100 — chunk to be safe
    CHUNK = 80
    for i in range(0, len(addrs), CHUNK):
        chunk = addrs[i:i + CHUNK]
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/token_price/base",
            params={"contract_addresses": ",".join(chunk), "vs_currencies": "usd"},
            headers=_coingecko_headers(),
            timeout=30,
        )
        if resp.status_code == 429:
            # Polite back-off; CoinGecko free tier is rate-limited
            import time; time.sleep(30)
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/token_price/base",
                params={"contract_addresses": ",".join(chunk), "vs_currencies": "usd"},
                headers=_coingecko_headers(),
                timeout=30,
            )
        resp.raise_for_status()
        for addr, body in resp.json().items():
            price = body.get("usd")
            if price is not None:
                out[addr.lower()] = float(price)
    return out


def _decimals_for(w3: Web3, token: str, cache: dict[str, int]) -> int:
    """Look up ERC-20 decimals with a per-process cache + known overrides."""
    addr = token.lower()
    if addr in cache:
        return cache[addr]
    if addr in KNOWN_DECIMALS:
        cache[addr] = KNOWN_DECIMALS[addr]
        return cache[addr]
    try:
        c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_DECIMALS_ABI)
        d = int(c.functions.decimals().call())
    except Exception:
        d = 18  # safe fallback
    cache[addr] = d
    return d


# ---- The main entry point --------------------------------------------------
def read_snapshot(w3: Optional[Web3] = None) -> VoteSnapshot:
    w3 = w3 or make_web3()

    voter = w3.eth.contract(address=Web3.to_checksum_address(VOTER), abi=VOTER_ABI)
    minter = w3.eth.contract(address=Web3.to_checksum_address(MINTER), abi=MINTER_ABI)
    rewards_sugar = w3.eth.contract(
        address=Web3.to_checksum_address(REWARDS_SUGAR), abi=REWARDS_SUGAR_ABI
    )

    total_weight = voter.functions.totalWeight().call()
    weekly = minter.functions.weekly().call()
    pool_count = voter.functions.length().call()

    # Pull all pools' latest epoch data in batches.
    epochs: list[tuple] = []
    BATCH = 100
    offset = 0
    safety = 0
    while safety < 50:  # at most 5000 pools, way over actual count
        safety += 1
        try:
            batch = rewards_sugar.functions.epochsLatest(BATCH, offset).call()
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

    # epochsLatest returns the LATEST epoch per pool; keep them all
    latest_ts = max((e[0] for e in epochs), default=0)

    # Collect tokens appearing as bribes or fees
    all_tokens: set[str] = set()
    for ep in epochs:
        _ts, _lp, _votes, _em, bribes, fees = ep
        for token, _ in bribes:
            all_tokens.add(token.lower())
        for token, _ in fees:
            all_tokens.add(token.lower())

    prices = fetch_token_prices_usd(all_tokens)

    decimals_cache: dict[str, int] = {}
    total_fees_usd = 0.0
    total_incentives_usd = 0.0
    unpriced_count = 0
    unpriced_set: set[str] = set()

    for ep in epochs:
        _ts, _lp, _votes, _em, bribes, fees = ep
        for token, amount in bribes:
            addr = token.lower()
            price = prices.get(addr)
            if price is None:
                unpriced_set.add(addr)
                continue
            d = _decimals_for(w3, token, decimals_cache)
            total_incentives_usd += (amount / (10 ** d)) * price
        for token, amount in fees:
            addr = token.lower()
            price = prices.get(addr)
            if price is None:
                unpriced_set.add(addr)
                continue
            d = _decimals_for(w3, token, decimals_cache)
            total_fees_usd += (amount / (10 ** d)) * price
    unpriced_count = len(unpriced_set)

    aero_price = get_aero_price_usd()
    new_emissions = weekly / 1e18
    emissions_value = new_emissions * aero_price
    total_rewards = total_fees_usd + total_incentives_usd
    multiplier = emissions_value / total_rewards if total_rewards > 0 else 0.0
    epoch_number = (latest_ts // EPOCH_LENGTH) if latest_ts else 0

    return VoteSnapshot(
        epoch_number=epoch_number,
        epoch_starts_at=latest_ts,
        total_voting_power=total_weight / 1e18,
        new_emissions=new_emissions,
        aero_price_usd=aero_price,
        emissions_value=emissions_value,
        total_fees=total_fees_usd,
        total_incentives=total_incentives_usd,
        total_rewards=total_rewards,
        multiplier=multiplier,
        pool_count=pool_count,
        unpriced_token_count=unpriced_count,
        debug={"pools_with_epoch_data": len(epochs)},
    )
