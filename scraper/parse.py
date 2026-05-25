"""Parse the rendered HTML of https://aerodrome.finance/vote.

The Aerodrome /vote page is a JS-rendered SPA. After Lightpanda renders it, we
extract the top-of-page summary (total voting power, fees, incentives, rewards,
new emission) and the per-pool list (for the epoch-winner job).

NOTE: The first deployment will almost certainly need parser tuning against the
real HTML. Set DEBUG_DUMP_HTML=1 and run with --dry-run to capture a snapshot,
then adjust the strategies below. Each labelled field has a `_extract_labelled`
call that tries label-adjacent-value first; if that fails it falls back to a
regex over the raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup


_NUM_RE = re.compile(
    r"""
    \$?\s*                      # optional $
    (?P<num>
        \d{1,3}(?:,\d{3})*(?:\.\d+)?   # 1,234,567.89
        |
        \d+(?:\.\d+)?                  # 1234.56
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
    total_voting_power: float
    total_fees: float
    total_incentives: float
    total_rewards: float
    new_emissions: float
    pools: list[Pool] = field(default_factory=list)


class ParseError(RuntimeError):
    pass


def parse_number(text: str) -> Optional[float]:
    """Parse a human-formatted number with optional $ prefix and K/M/B suffix."""
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


def _extract_labelled(soup: BeautifulSoup, label: str) -> Optional[float]:
    """Find an element whose text matches `label` (case-insensitive substring),
    then look at sibling/parent text for the numeric value."""
    label_lc = label.lower()

    # Strategy 1: any element whose direct text contains the label
    for el in soup.find_all(string=lambda t: t and label_lc in t.lower()):
        parent = el.parent
        if not parent:
            continue
        # Try siblings in document order
        for sib in list(parent.next_siblings) + list(parent.parent.children if parent.parent else []):
            if sib is el or sib is parent:
                continue
            txt = getattr(sib, "get_text", lambda: str(sib))()
            val = parse_number(txt)
            if val is not None:
                return val
        # Try the parent's own combined text minus the label
        parent_text = parent.get_text(" ", strip=True)
        cleaned = re.sub(re.escape(label), "", parent_text, flags=re.IGNORECASE)
        val = parse_number(cleaned)
        if val is not None:
            return val
    return None


def _extract_by_regex(text: str, label_pattern: str) -> Optional[float]:
    """Last-resort: find label then nearest number on the raw text."""
    m = re.search(label_pattern + r"[^0-9$]*([\d.,]+\s*[KMB]?)", text, re.IGNORECASE)
    if m:
        return parse_number(m.group(1))
    return None


def parse_summary(html: str) -> VotePage:
    """Extract the top-of-page totals. Raises ParseError if any are missing."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    labels = {
        "total_voting_power": ("Total Voting Power", r"total\s+voting\s+power"),
        "total_fees":         ("Total Fees",         r"total\s+fees"),
        "total_incentives":   ("Total Incentives",   r"total\s+incentives"),
        "total_rewards":      ("Total Rewards",      r"total\s+rewards"),
        "new_emissions":      ("New Emissions",      r"new\s+emissions?"),
    }

    extracted: dict[str, float] = {}
    missing: list[str] = []
    for key, (dom_label, regex_label) in labels.items():
        val = _extract_labelled(soup, dom_label)
        if val is None:
            val = _extract_by_regex(text, regex_label)
        if val is None:
            missing.append(dom_label)
        else:
            extracted[key] = val

    if missing:
        raise ParseError(
            f"Could not extract: {missing}. "
            "Set DEBUG_DUMP_HTML=1 and inspect debug_dumps/ to recalibrate parsers."
        )

    return VotePage(
        total_voting_power=extracted["total_voting_power"],
        total_fees=extracted["total_fees"],
        total_incentives=extracted["total_incentives"],
        total_rewards=extracted["total_rewards"],
        new_emissions=extracted["new_emissions"],
        pools=[],
    )


def parse_pools(html: str) -> list[Pool]:
    """Extract the per-pool rows (pair name + votes at minimum).

    Heuristic: find each pool row by looking for a pair label like "AERO/USDC"
    or "vAMM-X/Y", then read votes from the same row. Calibrate after first run.
    """
    soup = BeautifulSoup(html, "html.parser")
    pools: list[Pool] = []

    pair_re = re.compile(r"\b([sv]AMM-)?[A-Z0-9]{2,}/[A-Z0-9]{2,}\b")

    seen = set()
    for el in soup.find_all(string=pair_re):
        pair = pair_re.search(el).group(0)
        if pair in seen:
            continue
        # find the row container
        row = el.parent
        for _ in range(6):
            if row is None:
                break
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
