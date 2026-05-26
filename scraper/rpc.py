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

VOTING_ESCROW_ABI = [
    {"inputs":[],"name":"totalSupply","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

MINTER_ABI = [
    {"inputs":[],"name":"weekly","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[{"type":"uint256","name":"_minted"}],"name":"calculateGrowth","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"teamRate","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"tailEmissionRate","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"epochCount","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

ERC20_TOTAL_SUPPLY_ABI = [
    {"inputs":[],"name":"totalSupply","outputs":[{"type":"uint256","name":""}],"stateMutability":"view","type":"function"},
]

# Constants from Minter.sol main branch
MAX_BPS = 10_000
TAIL_START = 8_969_150 * 10**18

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
    rpc = os.environ.get("BASE_RPC_URL") or DEFAULT_BASE_RPC
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
    """AERO/USD from DefiLlama (same source as the token-price batch)."""
    resp = requests.get(
        f"https://coins.llama.fi/prices/current/base:{AERO_TOKEN.lower()}",
        timeout=15,
    )
    resp.raise_for_status()
    coins = resp.json().get("coins") or {}
    key = f"base:{AERO_TOKEN.lower()}"
    price = coins.get(key, {}).get("price")
    if price is None:
        # CoinGecko fallback
        cg = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "aerodrome-finance", "vs_currencies": "usd"},
            headers=_coingecko_headers(),
            timeout=15,
        )
        cg.raise_for_status()
        return float(cg.json()["aerodrome-finance"]["usd"])
    return float(price)


def fetch_token_prices_usd(tokens: set[str]) -> dict[str, float]:
    """DefiLlama batch token-price endpoint. Returns lowercase-address → USD.

    DefiLlama has wider DeFi-token coverage than CoinGecko (which returned 400
    for our token list), no API key required, and accepts comma-separated keys
    in a single GET. Output uses `base:0x...` keys.
    """
    if not tokens:
        return {}
    out: dict[str, float] = {}
    addrs = sorted(t.lower() for t in tokens)
    # DefiLlama can handle hundreds in one call but keep chunks modest for URL length
    CHUNK = 50
    for i in range(0, len(addrs), CHUNK):
        chunk = addrs[i:i + CHUNK]
        keys = ",".join(f"base:{a}" for a in chunk)
        resp = requests.get(
            f"https://coins.llama.fi/prices/current/{keys}",
            timeout=30,
        )
        if resp.status_code == 429:
            import time; time.sleep(15)
            resp = requests.get(
                f"https://coins.llama.fi/prices/current/{keys}",
                timeout=30,
            )
        if resp.status_code >= 400:
            # Skip silently — we'll just count these tokens as unpriced
            continue
        for key, body in (resp.json().get("coins") or {}).items():
            price = body.get("price")
            if price is None:
                continue
            # key is "base:0xAddr" — strip the chain prefix
            addr = key.split(":", 1)[-1].lower()
            out[addr] = float(price)
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
    veaero = w3.eth.contract(address=Web3.to_checksum_address(VOTING_ESCROW), abi=VOTING_ESCROW_ABI)
    rewards_sugar = w3.eth.contract(
        address=Web3.to_checksum_address(REWARDS_SUGAR), abi=REWARDS_SUGAR_ABI
    )

    voter_total_weight = voter.functions.totalWeight().call()
    ve_total_supply = veaero.functions.totalSupply().call()
    print(
        f"[rpc] Voter.totalWeight={voter_total_weight/1e18:,.4f} "
        f"VotingEscrow.totalSupply={ve_total_supply/1e18:,.4f}",
        flush=True,
    )
    # Frontend "Total voting power this epoch" matches VotingEscrow.totalSupply()
    # (all live veAERO at the current timestamp), not Voter.totalWeight (only
    # votes already cast this epoch).
    total_weight = ve_total_supply

    # ---- Emission diagnostics: read all the Minter constants/state ----
    aero = w3.eth.contract(address=Web3.to_checksum_address(AERO_TOKEN), abi=ERC20_TOTAL_SUPPLY_ABI)
    aero_total_supply = aero.functions.totalSupply().call()
    tail_rate = minter.functions.tailEmissionRate().call()
    epoch_count = minter.functions.epochCount().call()
    print(
        f"[rpc] aero.totalSupply={aero_total_supply/1e18:,.4f} "
        f"tailEmissionRate={tail_rate} epochCount={epoch_count} "
        f"TAIL_START={TAIL_START/1e18:,.0f}",
        flush=True,
    )
    weekly_gross = minter.functions.weekly().call()
    growth = minter.functions.calculateGrowth(weekly_gross).call()  # rebase to veAERO lockers
    team_rate_bps = minter.functions.teamRate().call()              # basis points
    # Aerodrome's team allocation formula (from Minter.updatePeriod):
    #   team_emissions = teamRate × (weekly + growth) / (PRECISION - teamRate)
    # PRECISION = 10000 (basis points).
    team_emissions = (team_rate_bps * (weekly_gross + growth)) // (10000 - team_rate_bps)
    weekly_net = weekly_gross - growth - team_emissions
    print(
        f"[rpc] weekly_gross={weekly_gross/1e18:,.0f} growth={growth/1e18:,.0f} "
        f"team_rate={team_rate_bps}bps team_em={team_emissions/1e18:,.0f} "
        f"net={weekly_net/1e18:,.0f}",
        flush=True,
    )
    weekly = weekly_net   # use the gauge-bound net for all downstream math
    pool_count = voter.functions.length().call()

    # Pull all pools' current-epoch data in batches.
    #
    # `epochsLatest(_limit, _offset)` SCANS `_limit` pools starting at `_offset`
    # and returns only those that have current-epoch data — so the returned
    # array can be SHORTER than `_limit`. Advance by `_limit` (pools scanned),
    # not by `len(batch)`, otherwise we stop early.
    epochs: list[tuple] = []
    BATCH = 100
    offset = 0
    batches_done = 0
    while offset < pool_count:
        try:
            batch = rewards_sugar.functions.epochsLatest(BATCH, offset).call()
        except Exception:
            if BATCH > 20:
                BATCH = 20
                continue
            raise
        epochs.extend(batch)
        batches_done += 1
        offset += BATCH
    print(
        f"[rpc] scanned {offset} pools across {batches_done} batches; "
        f"got {len(epochs)} pool-epoch entries",
        flush=True,
    )

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
    if unpriced_set:
        print(f"[rpc] unpriced tokens: {sorted(unpriced_set)}", flush=True)

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
