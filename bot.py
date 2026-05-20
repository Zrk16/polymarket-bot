"""
Polymarket Paper Trading Bot
=============================
Pulls live market data, asks Groq AI to spot mispricings,
places simulated bets, and tracks P&L in a persistent ledger.

Run:  python bot.py
Deploy: Railway with Procfile (worker: python bot.py)

Architecture based on patterns from:
  - Polymarket/agents (official, archived)
  - MrFadiAi/Polymarket-bot (multi-strategy)
  - warproxxx/poly-maker (market maker)
"""

import os
import sys
import time

from dotenv import load_dotenv

from ai import analyze_market
from decisions import log_decision
from fetcher import get_markets
from ledger import Ledger

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────
BANKROLL = float(os.getenv("BANKROLL", "20.0"))
BET_AMOUNT = float(os.getenv("BET_AMOUNT", "1.0"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.65"))
MARKETS_PER_CYCLE = int(os.getenv("MARKETS_PER_CYCLE", "20"))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "1800"))
MIN_LIQUIDITY = 1000  # Skip thin markets


def print_banner():
    print("=" * 55)
    print("  POLYMARKET PAPER TRADING BOT")
    print("=" * 55)
    print(f"  Bankroll:    ${BANKROLL:.2f}")
    print(f"  Bet size:    ${BET_AMOUNT:.2f}")
    print(f"  Confidence:  {CONFIDENCE_THRESHOLD}")
    print(f"  Cycle:       every {SLEEP_SECONDS // 60} min")
    print(f"  Markets:     top {MARKETS_PER_CYCLE} by liquidity")
    print("=" * 55)
    print()


def run_cycle(ledger, cycle_num):
    """One full cycle: resolve → fetch → analyze → bet → update."""

    print(f"\n{'─' * 55}")
    print(f"  Cycle {cycle_num} | {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'─' * 55}")

    # 1. Check if any open positions resolved
    ledger.check_resolutions()

    # 2. Fetch top markets by liquidity
    markets = get_markets(limit=MARKETS_PER_CYCLE)
    print(f"  Fetched {len(markets)} active markets")

    if not markets:
        print("  No markets available, skipping cycle")
        return

    # 3. Skip markets we already hold
    open_ids = ledger.get_open_condition_ids()
    candidates = [m for m in markets if m["condition_id"] not in open_ids]
    skipped = len(markets) - len(candidates)
    if skipped:
        print(f"  Skipping {skipped} markets with open positions")

    # 4. AI analysis + paper trading
    bets_placed = 0
    for market in candidates:
        if market["liquidity"] < MIN_LIQUIDITY:
            continue

        # Ask Groq AI for analysis
        decision = analyze_market(market)
        if decision is None:
            continue

        # Log EVERY decision to CSV (bet or not)
        log_decision(market, decision)

        # Skip low-confidence picks
        if not decision["should_bet"] or decision["confidence"] < CONFIDENCE_THRESHOLD:
            print(
                f"  SKIP  {market['question'][:45]}  "
                f"(conf={decision['confidence']:.2f})"
            )
            continue

        # Check bankroll
        if ledger.available_bankroll() < BET_AMOUNT:
            print("  ** Out of bankroll — skipping remaining markets **")
            break

        # Place paper trade
        direction = decision["direction"]
        entry_odds = (
            market["yes_price"] if direction == "YES" else market["no_price"]
        )
        token_id = (
            market["yes_token_id"] if direction == "YES" else market["no_token_id"]
        )

        # TODO: Real execution would go here —
        # 1. from py_clob_client import ClobClient
        # 2. client = ClobClient(host, key=key, chain_id=137)
        # 3. order = client.create_and_post_order(OrderArgs(
        #        token_id=token_id,
        #        price=entry_odds,
        #        size=BET_AMOUNT / entry_odds,
        #        side=BUY,
        #    ))
        # 4. Confirm fill, record actual execution price

        ledger.place_trade(
            market=market,
            direction=direction,
            entry_odds=entry_odds,
            token_id=token_id,
            bet_amount=BET_AMOUNT,
        )
        bets_placed += 1

        print(
            f"  BET {direction:3s} {market['question'][:45]}  "
            f"@ {entry_odds:.3f}  conf={decision['confidence']:.2f}"
        )
        print(f"         {decision['reasoning']}")

    # 5. Update unrealized P&L on all open positions
    ledger.update_unrealized_pnl()

    # 6. Print portfolio summary
    s = ledger.get_summary()
    print()
    print(f"  ┌─ Portfolio ────────────────────────────────────┐")
    print(f"  │  Total value:    ${s['total_value']:>8.2f}                  │")
    print(f"  │  Cash:           ${s['bankroll']:>8.2f}                  │")
    print(f"  │  Realized P&L:   ${s['realized_pnl']:>+8.2f}                  │")
    print(f"  │  Unrealized P&L: ${s['unrealized_pnl']:>+8.2f}                  │")
    print(f"  │  Open positions: {s['open_count']:>3d}                        │")
    print(f"  │  Resolved:       {s['resolved_count']:>3d}  "
          f"(W:{s['win_count']} / L:{s['loss_count']})              │")
    print(f"  │  Bets this cycle:{bets_placed:>3d}                        │")
    print(f"  └─────────────────────────────────────────────────┘")


def main():
    # Validate Groq API key early
    if not os.getenv("GROQ_API_KEY"):
        print("ERROR: GROQ_API_KEY not found in .env file")
        print("Copy .env.example to .env and add your key from console.groq.com")
        sys.exit(1)

    print_banner()

    ledger = Ledger(starting_bankroll=BANKROLL)
    cycle = 0

    print("Bot running. Ctrl+C to stop.\n")

    while True:
        cycle += 1
        try:
            run_cycle(ledger, cycle)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"\n  [error] Cycle {cycle} failed: {e}")
            print("  Continuing to next cycle...")

        print(f"\n  Sleeping {SLEEP_SECONDS // 60} minutes until next cycle...")

        try:
            time.sleep(SLEEP_SECONDS)
        except KeyboardInterrupt:
            print("\n\nShutting down. Final state saved to ledger.json.")
            s = ledger.get_summary()
            print(f"Final value: ${s['total_value']:.2f} "
                  f"(started ${s['starting_bankroll']:.2f})")
            break


if __name__ == "__main__":
    main()
