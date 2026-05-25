"""Lightpanda CDP-mode fetcher.

Starts `lightpanda serve` as a subprocess (CDP server), then drives it through
Playwright's `connect_over_cdp` API. This lets us use Playwright's rich
automation surface (init scripts, waits, request logging) on top of
Lightpanda's lighter JS engine — the same engine that worked for the original
OpenClaw scraper, before Aerodrome v5's wallet gate.

If the wallet gate still wipes our pre-seeded wagmi state during reconciliation,
the wagmi-store-overrides init script also monkey-patches Storage.removeItem and
the JSON.parse path that wagmi uses for its store, so that *our* seeded value
sticks no matter what wagmi tries to do.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
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

# Wallet-gate bypass: stub ethereum + pre-seed wagmi-drome store + lock the seed
# so wagmi's reconciliation can't wipe it back to "disconnected".
_INIT_SCRIPT = r"""
(() => {
    const fakeAddr = '0x000000000000000000000000000000000000dEaD';
    const baseChainId = '0x2105'; // 8453

    // ---- 1. Ethereum provider stub ----
    const provider = {
        isMetaMask: true,
        isConnected: () => true,
        chainId: baseChainId,
        networkVersion: '8453',
        selectedAddress: fakeAddr,
        request: async ({ method }) => {
            switch (method) {
                case 'eth_chainId':          return baseChainId;
                case 'eth_accounts':
                case 'eth_requestAccounts':  return [fakeAddr];
                case 'net_version':          return '8453';
                case 'wallet_getPermissions':
                case 'wallet_requestPermissions':
                    return [{ parentCapability: 'eth_accounts', caveats: [] }];
                case 'wallet_switchEthereumChain':
                case 'wallet_addEthereumChain':
                    return null;
                case 'personal_sign':
                case 'eth_sign':
                case 'eth_signTypedData_v4':
                    return '0x' + '00'.repeat(65);
                default: return null;
            }
        },
        on: () => {},
        removeListener: () => {},
        removeAllListeners: () => {},
        enable: async () => [fakeAddr],
    };
    provider.providers = [provider];
    Object.defineProperty(window, 'ethereum', { value: provider, writable: false, configurable: false });

    // ---- 2. Build the wagmi-drome store value ----
    const wagmiState = {
        state: {
            connections: { __type: 'Map', value: [
                ['injected-stub', {
                    accounts: [fakeAddr],
                    chainId: 8453,
                    connector: { id: 'injected', name: 'Injected', type: 'injected', uid: 'injected-stub' },
                }],
            ]},
            chainId: 8453,
            current: 'injected-stub',
        },
        version: 2,
    };
    const wagmiStoreJson = JSON.stringify(wagmiState);

    // ---- 3. Lock the seed in localStorage ----
    // wagmi calls localStorage.setItem during reconcile to write null/empty state.
    // Override setItem so that the wagmi-drome key cannot be overwritten with an
    // empty connections list, and so connection_status cannot be set to "disconnected".
    try {
        const realSet = Storage.prototype.setItem;
        const realRemove = Storage.prototype.removeItem;
        const realGet = Storage.prototype.getItem;

        Storage.prototype.setItem = function (key, value) {
            if (key && key.startsWith('wagmi-drome')) {
                // Always force our value
                return realSet.call(this, key, wagmiStoreJson);
            }
            if (key === '@appkit/connection_status') {
                return realSet.call(this, key, 'connected');
            }
            return realSet.call(this, key, value);
        };
        Storage.prototype.removeItem = function (key) {
            if (key && (key.startsWith('wagmi-drome') || key.startsWith('@appkit/'))) {
                return; // refuse deletes of our keys
            }
            return realRemove.call(this, key);
        };
        Storage.prototype.getItem = function (key) {
            if (key && key.startsWith('wagmi-drome')) {
                return wagmiStoreJson;
            }
            if (key === '@appkit/connection_status') return 'connected';
            if (key === '@appkit/active_namespace') return 'eip155';
            if (key === '@appkit/active_caip_network_id') return 'eip155:8453';
            return realGet.call(this, key);
        };

        // Seed initial values too (some readers cache early)
        realSet.call(localStorage, 'wagmi-drome-v5.0.0.store', wagmiStoreJson);
        realSet.call(localStorage, '@appkit/connection_status', 'connected');
        realSet.call(localStorage, '@appkit/active_namespace', 'eip155');
        realSet.call(localStorage, '@appkit/active_caip_network_id', 'eip155:8453');
        realSet.call(localStorage, 'disabledChains', '[]');
    } catch (e) {
        console.error('init script storage override failed', e);
    }
})();
"""


def _wait_for_port(host: str, port: int, timeout_s: float = 30.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise FetchError(f"lightpanda did not open {host}:{port} within {timeout_s}s")


def fetch(
    url: str,
    *,
    timeout_ms: int = 45000,
    wait_selector: str = "[data-test-amount]",
) -> str:
    """Drive Lightpanda (via CDP) to render `url` past Aerodrome's wallet gate."""
    binary = os.environ.get("LIGHTPANDA_PATH", "lightpanda")
    port = int(os.environ.get("LIGHTPANDA_PORT", "9222"))
    debug = os.environ.get("DEBUG_DUMP_HTML") == "1"

    server = subprocess.Popen(
        [binary, "serve", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    html: str = ""
    localstorage: Optional[str] = None
    network_log: list[dict] = []
    nav_error: Optional[str] = None
    lp_stderr: Optional[str] = None

    try:
        try:
            _wait_for_port("127.0.0.1", port)
        except FetchError:
            # Snapshot lightpanda's stderr to help diagnose startup failures
            try:
                lp_stderr = server.stderr.read(4096).decode("utf-8", errors="replace")
            except Exception:
                lp_stderr = None
            raise FetchError(
                f"lightpanda failed to start"
                + (f"; stderr: {lp_stderr}" if lp_stderr else "")
            )

        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            except Exception as e:
                raise FetchError(f"connect_over_cdp failed: {e}")

            # Lightpanda usually starts with a default browser context.
            context = browser.contexts[0] if browser.contexts else browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            context.add_init_script(_INIT_SCRIPT)

            page = context.pages[0] if context.pages else context.new_page()

            if debug:
                def _log_request(req):
                    if req.resource_type in {"document", "fetch", "xhr", "websocket"}:
                        network_log.append({
                            "method": req.method,
                            "url": req.url,
                            "resource_type": req.resource_type,
                        })
                page.on("request", _log_request)

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
                nav_error = (nav_error or "") + f"; page.content() failed: {e}"

            if debug:
                try:
                    localstorage = page.evaluate("() => JSON.stringify({...localStorage})")
                except Exception:
                    localstorage = None

            try:
                browser.close()
            except Exception:
                pass
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    if debug:
        Path("debug_dumps").mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        Path(f"debug_dumps/vote_{stamp}.html").write_text(html or "", encoding="utf-8")
        if localstorage:
            Path(f"debug_dumps/localstorage_{stamp}.json").write_text(
                localstorage, encoding="utf-8"
            )
        if network_log:
            import json as _json
            Path(f"debug_dumps/network_{stamp}.json").write_text(
                _json.dumps(network_log, indent=2), encoding="utf-8"
            )
        if lp_stderr:
            Path(f"debug_dumps/lightpanda_stderr_{stamp}.txt").write_text(
                lp_stderr, encoding="utf-8"
            )

    if not html or "data-test-amount" not in html:
        raise FetchError(
            f"No data-test-amount in {len(html)}-byte page"
            + (f" ({nav_error})" if nav_error else "")
        )

    return html
