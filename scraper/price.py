"""AERO price from CoinGecko public API."""

from __future__ import annotations

import os
from typing import Optional

import requests

COINGECKO_AERO_ID = "aerodrome-finance"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"


def get_aero_usd(api_key: Optional[str] = None, timeout: int = 15) -> float:
    headers = {}
    key = api_key or os.environ.get("COINGECKO_API_KEY")
    if key:
        headers["x-cg-demo-api-key"] = key

    resp = requests.get(
        COINGECKO_URL,
        params={"ids": COINGECKO_AERO_ID, "vs_currencies": "usd"},
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    price = data.get(COINGECKO_AERO_ID, {}).get("usd")
    if price is None:
        raise RuntimeError(f"CoinGecko response missing AERO/usd: {data!r}")
    return float(price)
