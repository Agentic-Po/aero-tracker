"""Thin wrapper around the Lightpanda CLI to fetch a JS-rendered page.

Uses --wait-until networkidle and --wait-ms so the SPA's initial data fetch
completes before the HTML is dumped. These flags were the trick that made the
original OpenClaw script work past Aerodrome's wallet-connect overlay.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


class LightpandaError(RuntimeError):
    pass


def fetch(
    url: str,
    timeout: int = 90,
    lightpanda_path: Optional[str] = None,
    wait_until: str = "networkidle",
    wait_ms: int = 20000,
    http_timeout_ms: int = 20000,
) -> str:
    """Run `lightpanda fetch --dump html ... <url>` and return rendered HTML.

    Raises LightpandaError on non-zero exit or suspiciously small output.
    """
    binary = lightpanda_path or os.environ.get("LIGHTPANDA_PATH", "lightpanda")

    cmd = [
        binary, "fetch",
        "--dump", "html",
        "--wait-until", wait_until,
        "--wait-ms", str(wait_ms),
        "--http-timeout", str(http_timeout_ms),
        url,
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

    if proc.returncode != 0:
        raise LightpandaError(
            f"lightpanda exited {proc.returncode}: {proc.stderr.strip() or '<no stderr>'}"
        )

    html = proc.stdout
    if not html or len(html) < 500:
        raise LightpandaError(
            f"lightpanda returned suspiciously small output ({len(html)} bytes); "
            f"stderr: {proc.stderr.strip()}"
        )

    if os.environ.get("DEBUG_DUMP_HTML") == "1":
        Path("debug_dumps").mkdir(exist_ok=True)
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        Path(f"debug_dumps/vote_{stamp}.html").write_text(html, encoding="utf-8")

    return html
