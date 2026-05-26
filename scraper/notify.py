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

    title = "<b>Aero Multiplier · 8h snapshot</b>"
    if epoch_number is not None:
        title += f" · ep{epoch_number}"

    lines = [
        title,
        "",
        f"<b>Multiplier:</b> {multiplier:.3f}×{delta}{warn}",
        f"<b>AERO price:</b> ${aero_price_usd:,.4f}",
        "",
        f"Total VP:      {_fmt_num(total_voting_power) if total_voting_power else '—'} veAERO",
        f"Total Fees:    ${_fmt_num(total_fees) if total_fees else '—'}",
        f"Incentives:    ${_fmt_num(total_incentives) if total_incentives else '—'}",
        f"Total Rewards: ${_fmt_num(total_rewards)}",
        f"New Emissions: {_fmt_num(new_emissions)} AERO",
        "",
        "<b>If incentives +$:</b>",
        f"  +1k    → {sim_plus_1k:.3f}×",
        f"  +25k   → {sim_plus_25k:.3f}×",
        f"  +50k   → {sim_plus_50k:.3f}×",
        f"  +100k  → {sim_plus_100k:.3f}×",
    ]
    if unpriced_token_count:
        lines.append("")
        lines.append(f"<i>note: {unpriced_token_count} reward token(s) had no CoinGecko price and were skipped</i>")
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
