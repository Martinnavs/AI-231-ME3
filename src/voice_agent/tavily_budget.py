"""Tavily's free plan is 1,000 credits a month; the owner had used 80 % of them on the demo day
(2026-11-12), partly through benchmark runs. This module knows the thresholds and reads the usage
from Tavily's own free /usage endpoint. It needs only httpx, so the scripts that are about to
spend credits (the evaluations) can ask before they start.

Below CONSERVE_AT everything works as before; from CONSERVE_AT one query and fewer results per
research call; from RESERVE_AT no web research and no Tavily answers (Google's grounded answers and
Wikipedia remain). TAVILY_MODE=normal|conserve|reserve forces a mode.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

USAGE_URL = "https://api.tavily.com/usage"
PLAN_LIMIT = 1000
CONSERVE_AT = float(os.environ.get("TAVILY_CONSERVE_AT", "0.70"))
RESERVE_AT = float(os.environ.get("TAVILY_RESERVE_AT", "0.90"))
MODES = ("normal", "conserve", "reserve")
KEY_FILE = Path(os.environ.get("TAVILY_KEY_FILE", Path.home() / ".secrets/tavily/api-key.txt"))


def mode_for(used: float, limit: float) -> str:
    share = used / limit if limit else 0.0
    return "reserve" if share >= RESERVE_AT else "conserve" if share >= CONSERVE_AT else "normal"


def forced_mode() -> str | None:
    forced = os.environ.get("TAVILY_MODE", "")
    return forced if forced in MODES else None


def _key() -> str | None:
    key = os.environ.get("TAVILY_API_KEY")
    if key and key.strip():
        return key.strip()
    try:
        return KEY_FILE.read_text().strip() or None
    except OSError:
        return None


def credit_status() -> tuple[int | None, int, str]:
    """(credits used, plan limit, mode); (None, limit, "normal") when /usage cannot be read."""
    if (forced := forced_mode()) is not None:
        return None, PLAN_LIMIT, forced
    try:
        account = httpx.get(
            USAGE_URL, headers={"Authorization": f"Bearer {_key()}"}, timeout=5.0
        ).json()["account"]
        used, limit = int(account["plan_usage"]), int(account.get("plan_limit") or PLAN_LIMIT)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None, PLAN_LIMIT, "normal"
    return used, limit, mode_for(used, limit)
