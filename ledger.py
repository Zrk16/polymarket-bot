"""
Persistent ledger — tracks every paper trade in ledger.json.

P&L math (prediction market basics):
  - You buy "shares" of an outcome (YES or NO).
  - Share price = current probability (e.g., YES at $0.60).
  - Shares purchased = bet_amount / share_price.
  - If your outcome wins: each share pays $1.00. Profit = shares - bet.
  - If your outcome loses: shares worth $0. Loss = full bet amount.
  - Unrealized P&L = (shares * current_price) - bet_amount.
"""

import json
import os
import time
import uuid

from fetcher import get_live_price, is_market_resolved

LEDGER_FILE = "ledger.json"


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
        if os.path.exists(LEDGER_FILE):
            with open(LEDGER_FILE, "r") as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(LEDGER_FILE, "w") as f:
            json.dump(self.data, f, indent=2)

    def available_bankroll(self):
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

        # TODO: Real execution would go here —
        # 1. Build order via py-clob-client
        # 2. Sign with wallet private key
        # 3. Submit to CLOB API: POST /order
        # 4. Wait for fill confirmation
        # 5. Record actual fill price (may differ from midpoint)
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
            "status": "open",
            "final_pnl": None,
        }

        self.data["trades"].append(trade)
        self.data["bankroll"] -= bet_amount
        self._save()
        return trade

    def update_unrealized_pnl(self):
        """
        Fetch live prices for all open positions and recalculate
        unrealized P&L: (shares * current_price) - bet_amount.
        """
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
        """
        Check each open position — has the market resolved?
        If yes, calculate final P&L and close the trade.

        # TODO: Real bot would also:
        # 1. Call redeem endpoint to claim winnings
        # 2. Verify on-chain settlement
        # 3. Update wallet USDC balance
        """
        for trade in self.data["trades"]:
            if trade["status"] != "open":
                continue

            resolved, winner = is_market_resolved(trade["market_id"])
            if not resolved:
                continue

            if winner is None:
                # Market voided — refund the bet
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
