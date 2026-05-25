"""Playwright-based fetcher.

Aerodrome v5 added a hard wallet-connect gate on /vote, which blocks naive
headless fetchers (including Lightpanda nightly). This module loads the page in
real Chromium and tries multiple bypass strategies in order:

  1. Inject a stub `window.ethereum` provider with a Base-chain dead address.
  2. Pre-seed wagmi localStorage entries hinting at a connected state.
  3. Wait for the `[data-test-amount]` selector that Aerodrome uses for every
     numeric field on /vote.

If `[data-test-amount]` is not present in the final HTML, we raise so the
workflow uploads the dump for diagnosis.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright


class FetchError(RuntimeError):
    pass


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Stub Ethereum provider — Base mainnet chainId 0x2105, fixed dead address.
# This satisfies wagmi's "is a wallet present?" check without ever signing.
_INIT_SCRIPT = """
const fakeAddr = '0x000000000000000000000000000000000000dEaD';
const baseChainId = '0x2105'; // 8453
window.ethereum = {
    isMetaMask: true,
    isConnected: () => true,
    chainId: baseChainId,
    selectedAddress: fakeAddr,
    request: async ({ method, params }) => {
        switch (method) {
            case 'eth_chainId': return baseChainId;
            case 'eth_accounts':
            case 'eth_requestAccounts': return [fakeAddr];
            case 'wallet_switchEthereumChain':
            case 'wallet_addEthereumChain': return null;
            case 'net_version': return '8453';
            default: return null;
        }
    },
    on: () => {},
    removeListener: () => {},
    enable: async () => [fakeAddr],
};
// Hint at a connected wagmi state — keys are best-effort guesses
try {
    localStorage.setItem('wagmi.connected', 'true');
    localStorage.setItem('wagmi.wallet', 'injected');
} catch (e) {}
"""


def fetch(
    url: str,
    *,
    timeout_ms: int = 45000,
    wait_selector: str = "[data-test-amount]",
    headless: bool = True,
) -> str:
    """Load URL in headless Chromium with wallet-gate bypass and return HTML."""
    debug = os.environ.get("DEBUG_DUMP_HTML") == "1"
    html: Optional[str] = None
    localstorage: Optional[str] = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        context.add_init_script(_INIT_SCRIPT)
        page = context.new_page()

        nav_error: Optional[str] = None
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_selector(wait_selector, timeout=timeout_ms, state="attached")
            except PlaywrightTimeout:
                nav_error = f"selector '{wait_selector}' not found within {timeout_ms}ms"
        except Exception as e:
            nav_error = f"goto failed: {e}"

        try:
            html = page.content()
        except Exception as e:
            nav_error = f"page.content() failed: {e}"
            html = ""

        if debug:
            try:
                localstorage = page.evaluate("() => JSON.stringify({...localStorage})")
            except Exception:
                localstorage = None

        browser.close()

    if html is None:
        html = ""

    if debug:
        Path("debug_dumps").mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        Path(f"debug_dumps/vote_{stamp}.html").write_text(html, encoding="utf-8")
        if localstorage:
            Path(f"debug_dumps/localstorage_{stamp}.json").write_text(
                localstorage, encoding="utf-8"
            )

    if "data-test-amount" not in html:
        raise FetchError(
            f"No data-test-amount elements in {len(html)}-byte page"
            + (f" ({nav_error})" if nav_error else "")
            + ". Likely still on wallet gate — check debug_dumps/."
        )

    return html
