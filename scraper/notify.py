"""Telegram Bot API messages — plain requests, no extra deps."""

from __future__ import annotations

import os

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _fmt_num(n: float, decimals: int = 2) -> str:
    if abs(n) >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n/1_000:.2f}K"
    return f"{n:.{decimals}f}"


def _fmt_precise(n: float | None) -> str:
    """Full-integer + short form, e.g. '1,003,295,086 (≈ 1.0B)'."""
    if n is None:
        return "—"
    return f"{n:,.0f} (≈ {_fmt_num(n)})"


def send(text: str, *, parse_mode: str = "HTML", disable_preview: bool = True) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        TELEGRAM_API.format(token=token),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        },
        timeout=15,
    )
    resp.raise_for_status()


def format_8h_message(
    *,
    epoch_number: int | None = None,
    multiplier: float,
    prev_multiplier: float | None,
    total_voting_power: float | None,
    total_fees: float | None,
    total_incentives: float | None,
    total_rewards: float,
    new_emissions: float,
    aero_price_usd: float,
    sim_plus_1k: float,
    sim_plus_25k: float,
    sim_plus_50k: float,
    sim_plus_100k: float,
    unpriced_token_count: int = 0,
) -> str:
    delta = ""
    if prev_multiplier is not None:
        d = multiplier - prev_multiplier
        arrow = "▲" if d > 0 else ("▼" if d < 0 else "▬")
        delta = f"  ({arrow} {d:+.3f})"

    warn = "  ⚠️ <b>below 1.1</b>" if multiplier < 1.1 else ""

    title = "🚧 <b>Aero Multiplier · 8h snapshot</b> 🚧"
    if epoch_number is not None:
        title += f" · ep{epoch_number}"

    emissions_value = new_emissions * aero_price_usd

    lines = [
        title,
        "",
        "<b>✓ MATCHES Aerodrome /vote:</b>",
        f"Total VP:       {_fmt_precise(total_voting_power)} veAERO",
        f"New Emissions:  {_fmt_precise(new_emissions)} AERO",
        f"AERO price:     ${aero_price_usd:,.4f}",
        f"Emissions val:  ${_fmt_precise(emissions_value)}",
        f"Incentives:     ${_fmt_precise(total_incentives)}",
        "",
        "<b>⚠ Partial (v2 pools only — CL pool fees still missing):</b>",
        f"Total Fees:     ${_fmt_precise(total_fees)}",
        f"Total Rewards:  ${_fmt_precise(total_rewards)}",
        f"Multiplier:     {multiplier:.3f}×{delta}{warn}",
        "",
        "<i>Phase 2.2 — using velodrome-finance/sugar-sdk. "
        "Total Fees currently sums v2 pools only (8,993 v2 / 0 CL returned by SDK). "
        "Slipstream/CL pool fees will be added in Phase 3.</i>",
    ]
    return "\n".join(lines)


def format_epoch_winner_message(
    *,
    epoch_number: int | None,
    pair: str,
    votes: float,
    total_votes: float,
    pct_of_total: float,
    is_ignition: bool,
) -> str:
    ep = f"Epoch {epoch_number}" if epoch_number is not None else "This epoch"
    ignition = " 🔥 (Ignition)" if is_ignition else ""
    return (
        f"<b>🏆 {ep} winner · 1h before flip</b>\n\n"
        f"<b>Pool:</b>  {pair}{ignition}\n"
        f"<b>Votes:</b> {_fmt_num(votes)} veAERO\n"
        f"<b>Share:</b> {pct_of_total:.2f}% of total\n"
    )
