"""
Polymarket API client — no auth needed for reading market data.

Two APIs used:
  - Gamma API: market metadata (questions, descriptions, prices)
  - CLOB API:  live orderbook prices, resolution status
"""

import json
import requests

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


def get_markets(limit=50):
    """
    Fetch active, open markets sorted by liquidity (highest first).
    Returns list of parsed market dicts.
    """
    markets = []
    offset = 0

    while len(markets) < limit:
        batch_size = min(limit - len(markets), 100)
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": batch_size,
                    "offset": offset,
                    "order": "liquidity",
                    "ascending": "false",
                },
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()
        except Exception as e:
            print(f"  [fetcher] Error fetching markets: {e}")
            break

        for raw in batch:
            parsed = _parse_market(raw)
            if parsed:
                markets.append(parsed)

        if len(batch) < batch_size:
            break
        offset += batch_size

    return markets


def _parse_market(m):
    """
    Parse raw Gamma API response into a clean dict.

    IMPORTANT: outcomePrices and clobTokenIds come back as JSON-encoded
    strings (not arrays). Must json.loads() them first.
    """
    try:
        prices = json.loads(m.get("outcomePrices", "[]"))
        token_ids = json.loads(m.get("clobTokenIds", "[]"))

        # Only handle binary YES/NO markets
        if len(prices) < 2 or len(token_ids) < 2:
            return None

        yes_price = float(prices[0])
        no_price = float(prices[1])

        # Skip markets with broken prices
        if yes_price <= 0 or no_price <= 0:
            return None

        return {
            "condition_id": m.get("conditionId", ""),
            "market_id": m.get("id", ""),
            "question": m.get("question", ""),
            "description": (m.get("description") or "")[:500],
            "yes_price": yes_price,
            "no_price": no_price,
            "yes_token_id": token_ids[0],
            "no_token_id": token_ids[1],
            "liquidity": float(m.get("liquidity", 0)),
            "volume": float(m.get("volume", 0)),
            "end_date": m.get("endDate", ""),
            "slug": m.get("slug", ""),
        }
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def get_live_price(token_id):
    """
    Fetch current midpoint price for a token from CLOB API.
    Returns float (0.0–1.0) or None on failure.
    """
    try:
        resp = requests.get(
            f"{CLOB_API}/midpoint",
            params={"token_id": token_id},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["mid"])
    except Exception:
        return None


def is_market_resolved(condition_id):
    """
    Check if a market has resolved via the CLOB API.
    Returns (is_resolved, winner) where winner is "YES", "NO", or None.
    """
    try:
        resp = requests.get(
            f"{CLOB_API}/markets/{condition_id}",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("closed", False):
            return False, None

        for token in data.get("tokens", []):
            if token.get("winner", False):
                outcome = token.get("outcome", "").upper()
                if outcome in ("YES", "NO"):
                    return True, outcome

        # Market closed but no clear winner (voided/cancelled)
        return True, None
    except Exception:
        return False, None
