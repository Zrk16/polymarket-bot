"""
Persistent ledger — tracks every paper trade in ledger.json.

P&L math (prediction market basics):
  - You buy "shares" of an outcome (YES or NO).
  - Share price = current probability (e.g., YES at $0.60).
  - Shares purchased = bet_amount / share_price.
  - If your outcome wins: each share pays $1.00. Profit = shares - bet.
  - If your outcome loses: shares worth $0. Loss = full bet amount.
  - Unrealized P&L = (shares * current_price) - bet_amount.

Persistence:
  - Saves to ledger.json locally.
  - If HF_TOKEN + HF_DATASET_REPO env vars are set, also syncs to a
    Hugging Face dataset repo so data survives Space rebuilds.
"""

import json
import os
import time
import uuid

from fetcher import get_live_price, is_market_resolved

LEDGER_FILE = "ledger.json"

_HF_TOKEN = os.getenv("HF_TOKEN")
_HF_REPO = os.getenv("HF_DATASET_REPO")  # e.g. "zrk2010/polymarket-data"


def _hf_push(local_path):
    """Push ledger.json to HF Dataset repo (fire-and-forget, never crashes)."""
    if not (_HF_TOKEN and _HF_REPO):
        return
    try:
        from huggingface_hub import HfApi
        HfApi(token=_HF_TOKEN).upload_file(
            path_or_fileobj=local_path,
            path_in_repo="ledger.json",
            repo_id=_HF_REPO,
            repo_type="dataset",
        )
    except Exception as e:
        print(f"  [ledger] HF sync failed: {e}")


def _hf_pull():
    """Pull ledger.json from HF Dataset repo. Returns dict or {}."""
    if not (_HF_TOKEN and _HF_REPO):
        return {}
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id=_HF_REPO,
            filename="ledger.json",
            repo_type="dataset",
            token=_HF_TOKEN,
        )
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


class Ledger:
    def __init__(self, starting_bankroll=20.0):
        self.data = self._load()

        if "bankroll" not in self.data:
            self.data["bankroll"] = starting_bankroll
            self.data["starting_bankroll"] = starting_bankroll
            self.data["realized_pnl"] = 0.0
            self.data["trades"] = []
            self._save()

    def _load(self):
        # 1. Try HF Dataset (survives rebuilds)
        data = _hf_pull()
        if data:
            print("  [ledger] Loaded from HF Dataset")
            return data
        # 2. Fall back to local file
        if os.path.exists(LEDGER_FILE):
            with open(LEDGER_FILE, "r") as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(LEDGER_FILE, "w") as f:
            json.dump(self.data, f, indent=2)
        _hf_push(LEDGER_FILE)

    def available_bankroll(self):
        return self.data["bankroll"]

    def add_funds(self, amount: float):
        """Add paper money to the bankroll (dashboard top-up)."""
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValueError("Amount must be positive")
        self.data["bankroll"] = round(self.data["bankroll"] + amount, 2)
        self.data["starting_bankroll"] = round(
            self.data["starting_bankroll"] + amount, 2
        )
        self._save()
        return self.data["bankroll"]

    def get_open_condition_ids(self):
        return {
            t["market_id"]
            for t in self.data["trades"]
            if t["status"] == "open"
        }

    def place_trade(self, market, direction, entry_odds, token_id, bet_amount):
        """
        Record a paper trade. Deducts bet_amount from bankroll.
        """
        shares = round(bet_amount / entry_odds, 6)

        trade = {
            "trade_id": str(uuid.uuid4())[:8],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "market_id": market["condition_id"],
            "market_title": market["question"],
            "direction": direction,
            "token_id": token_id,
            "entry_odds": entry_odds,
            "bet_amount": bet_amount,
            "shares": shares,
            "current_odds": entry_odds,
            "unrealized_pnl": 0.0,
            "end_date": market.get("end_date", ""),
            "status": "open",
            "final_pnl": None,
            "resolved_at": None,
        }

        self.data["trades"].append(trade)
        self.data["bankroll"] -= bet_amount
        self.data["bankroll"] = round(self.data["bankroll"], 2)
        self._save()
        return trade

    def update_unrealized_pnl(self):
        """Fetch live prices for all open positions and recalculate unrealized P&L."""
        for trade in self.data["trades"]:
            if trade["status"] != "open":
                continue

            price = get_live_price(trade["token_id"])
            if price is None:
                continue

            current_value = trade["shares"] * price
            trade["current_odds"] = round(price, 4)
            trade["unrealized_pnl"] = round(current_value - trade["bet_amount"], 4)

        self._save()

    def check_resolutions(self):
        """Check each open position — has the market resolved?"""
        for trade in self.data["trades"]:
            if trade["status"] != "open":
                continue

            resolved, winner = is_market_resolved(trade["market_id"])
            if not resolved:
                continue

            if winner is None:
                trade["status"] = "voided"
                trade["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                trade["final_pnl"] = 0.0
                trade["unrealized_pnl"] = 0.0
                self.data["bankroll"] += trade["bet_amount"]
                print(f"  VOIDED [{trade['market_title'][:50]}] — bet refunded")
                continue

            if winner == trade["direction"]:
                payout = trade["shares"] * 1.0
                final_pnl = payout - trade["bet_amount"]
                self.data["bankroll"] += payout
                label = "WIN"
            else:
                final_pnl = -trade["bet_amount"]
                label = "LOSS"

            trade["status"] = "resolved"
            trade["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            trade["final_pnl"] = round(final_pnl, 4)
            trade["current_odds"] = 1.0 if winner == trade["direction"] else 0.0
            trade["unrealized_pnl"] = 0.0
            self.data["realized_pnl"] = round(
                self.data["realized_pnl"] + final_pnl, 4
            )

            print(
                f"  RESOLVED [{trade['market_title'][:50]}] "
                f"{label} | P&L: ${final_pnl:+.2f}"
            )

        self._save()

    def get_summary(self):
        open_trades = [t for t in self.data["trades"] if t["status"] == "open"]
        unrealized = sum(t["unrealized_pnl"] for t in open_trades)
        invested = sum(t["bet_amount"] for t in open_trades)

        return {
            "bankroll": self.data["bankroll"],
            "starting_bankroll": self.data["starting_bankroll"],
            "total_value": round(self.data["bankroll"] + invested + unrealized, 4),
            "realized_pnl": self.data["realized_pnl"],
            "unrealized_pnl": round(unrealized, 4),
            "open_count": len(open_trades),
            "resolved_count": sum(
                1 for t in self.data["trades"] if t["status"] == "resolved"
            ),
            "total_trades": len(self.data["trades"]),
            "win_count": sum(
                1
                for t in self.data["trades"]
                if t["status"] == "resolved" and t["final_pnl"] and t["final_pnl"] > 0
            ),
            "loss_count": sum(
                1
                for t in self.data["trades"]
                if t["status"] == "resolved"
                and t["final_pnl"] is not None
                and t["final_pnl"] < 0
            ),
        }
