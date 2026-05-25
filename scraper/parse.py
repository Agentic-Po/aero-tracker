"""Parse the rendered HTML of https://aerodrome.finance/vote.

Two strategies, in order of preference:

1. `data-test-amount="..."` — Aerodrome's frontend tags every formatted number
   with this attribute, value = raw unformatted decimal. Most reliable.

2. Label-then-nearest-dollar-amount — fallback when no data-test-amount tag is
   adjacent to the label (used by the original aero-multiplier.sh for Total
   Rewards).

new_emissions + total_rewards are REQUIRED. Other fields (voting power, fees,
incentives) are optional — we'll return None if not present and let the dashboard
display "—".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup


_NUM_RE = re.compile(
    r"""
    \$?\s*
    (?P<num>
        \d{1,3}(?:,\d{3})*(?:\.\d+)?
        |
        \d+(?:\.\d+)?
    )
    \s*
    (?P<suffix>[KMB])?
    """,
    re.VERBOSE,
)

_SUFFIX_MULT = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


@dataclass
class Pool:
    pair: str
    votes: float
    fees_usd: Optional[float] = None
    incentives_usd: Optional[float] = None
    rewards_usd: Optional[float] = None
    pool_address: Optional[str] = None


@dataclass
class VotePage:
    new_emissions: float
    total_rewards: float
    total_voting_power: Optional[float] = None
    total_fees: Optional[float] = None
    total_incentives: Optional[float] = None
    pools: list[Pool] = field(default_factory=list)


class ParseError(RuntimeError):
    pass


def parse_number(text: str) -> Optional[float]:
    if not text:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    raw = m.group("num").replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    suffix = m.group("suffix")
    if suffix:
        value *= _SUFFIX_MULT[suffix]
    return value


def _extract_data_test_amount(html: str, label: str) -> Optional[float]:
    """Find the first data-test-amount attribute appearing after `label`.

    Matches the technique used by the original aero-multiplier.sh:
      grep -oP 'New Emissions:.*?<span[^>]*data-test-amount="\K[^"]+'
    """
    pattern = re.escape(label) + r'.*?data-test-amount="([^"]+)"'
    m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_dollar_after_label(html: str, label_pattern: str) -> Optional[float]:
    """Find a $-prefixed number on the page after a label match."""
    pattern = label_pattern + r".*?\$([\d,]+(?:\.\d+)?)\s*([KMB])?"
    m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if m.group(2):
        value *= _SUFFIX_MULT[m.group(2)]
    return value


def parse_summary(html: str) -> VotePage:
    """Extract top-of-page totals. new_emissions and total_rewards are required."""
    # ---- New Emissions (required) ----
    new_emissions = _extract_data_test_amount(html, "New Emissions")
    if new_emissions is None:
        # fallback: look for "New Emissions" followed by N (K/M/B optional)
        m = re.search(
            r"new\s+emissions?[:\s]*([\d,]+(?:\.\d+)?)\s*([KMB])?",
            html, re.IGNORECASE | re.DOTALL,
        )
        if m:
            new_emissions = float(m.group(1).replace(",", ""))
            if m.group(2):
                new_emissions *= _SUFFIX_MULT[m.group(2)]

    # ---- Total Rewards (required) ----
    total_rewards = _extract_data_test_amount(html, "Total Rewards")
    if total_rewards is None:
        total_rewards = _extract_dollar_after_label(html, r"total\s+rewards")

    # ---- Optional fields ----
    total_voting_power = (
        _extract_data_test_amount(html, "Total Voting Power")
        or _extract_data_test_amount(html, "Voting Power")
    )
    total_fees = (
        _extract_data_test_amount(html, "Total Fees")
        or _extract_dollar_after_label(html, r"total\s+fees")
    )
    total_incentives = (
        _extract_data_test_amount(html, "Total Incentives")
        or _extract_dollar_after_label(html, r"total\s+incentives")
    )

    missing = []
    if new_emissions is None: missing.append("New Emissions")
    if total_rewards is None: missing.append("Total Rewards")

    if missing:
        raise ParseError(
            f"Required fields missing: {missing}. "
            "Set DEBUG_DUMP_HTML=1 and inspect debug_dumps/ to recalibrate."
        )

    return VotePage(
        new_emissions=new_emissions,
        total_rewards=total_rewards,
        total_voting_power=total_voting_power,
        total_fees=total_fees,
        total_incentives=total_incentives,
        pools=[],
    )


def parse_pools(html: str) -> list[Pool]:
    """Extract per-pool rows (pair name + votes at minimum).

    Heuristic: look for ticker/ticker patterns and pull data-test-amount nearby.
    Likely needs calibration after the first epoch-winner run.
    """
    soup = BeautifulSoup(html, "html.parser")
    pools: list[Pool] = []

    pair_re = re.compile(r"\b([sv]AMM-)?[A-Z0-9]{2,}/[A-Z0-9]{2,}\b")

    seen = set()
    for el in soup.find_all(string=pair_re):
        pair = pair_re.search(el).group(0)
        if pair in seen:
            continue
        row = el.parent
        for _ in range(6):
            if row is None:
                break
            row_html = str(row)
            # Try data-test-amount first
            m = re.search(r'data-test-amount="([^"]+)"', row_html)
            if m:
                try:
                    votes = float(m.group(1).replace(",", ""))
                    pools.append(Pool(pair=pair, votes=votes))
                    seen.add(pair)
                    break
                except ValueError:
                    pass
            row_text = row.get_text(" ", strip=True)
            votes_m = re.search(r"([\d.,]+\s*[KMB]?)\s*(?:votes|veAERO|VP)", row_text, re.IGNORECASE)
            if votes_m:
                votes = parse_number(votes_m.group(1))
                if votes is not None:
                    pools.append(Pool(pair=pair, votes=votes))
                    seen.add(pair)
                    break
            row = row.parent

    return pools


def parse_full(html: str) -> VotePage:
    summary = parse_summary(html)
    summary.pools = parse_pools(html)
    return summary
