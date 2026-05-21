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
from datetime import datetime, timezone

from dotenv import load_dotenv

from ai import analyze_market
from decisions import log_decision
from fetcher import get_markets
from ledger import Ledger

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────
BANKROLL = float(os.getenv("BANKROLL", "20.0"))
BET_AMOUNT = float(os.getenv("BET_AMOUNT", "1.0"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.55"))
MARKETS_PER_CYCLE = int(os.getenv("MARKETS_PER_CYCLE", "50"))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "1800"))
MIN_LIQUIDITY = 1000  # Skip thin markets
MAX_BET_FRACTION = float(os.getenv("MAX_BET_FRACTION", "0.10"))  # Max % of bankroll per bet
MAX_DAYS_TO_CLOSE = int(os.getenv("MAX_DAYS_TO_CLOSE", "14"))    # Skip markets closing further than this
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "20"))  # Never hold more than this many bets at once
BANKROLL_RESERVE = float(os.getenv("BANKROLL_RESERVE", "0.20"))  # Always keep this fraction of starting bankroll as cash


def kelly_bet(confidence, entry_odds, bankroll):
    """
    Half-Kelly bet sizing. Bets more when AI is more confident.

    Kelly formula: f = (p*b - (1-p)) / b
      p = AI confidence (probability we're right)
      b = net payout per $1 risked = (1 - entry_odds) / entry_odds
    Half-Kelly = f * 0.5  (safer, reduces variance)

    Result clamped between BET_AMOUNT and MAX_BET_FRACTION of bankroll.
    """
    if entry_odds <= 0 or entry_odds >= 1:
        return BET_AMOUNT

    b = (1 - entry_odds) / entry_odds  # net odds
    f = (confidence * b - (1 - confidence)) / b  # Kelly fraction
    f = max(0, f) * 0.5  # Half-Kelly, floor at 0

    raw = f * bankroll
    # Clamp: never less than base bet, never more than MAX_BET_FRACTION of bankroll
    return round(
        max(BET_AMOUNT, min(raw, bankroll * MAX_BET_FRACTION)),
        2
    )


def print_banner():
    print("=" * 55)
    print("  POLYMARKET PAPER TRADING BOT")
    print("=" * 55)
    print(f"  Bankroll:    ${BANKROLL:.2f}")
    print(f"  Bet size:    ${BET_AMOUNT:.2f}")
    print(f"  Confidence:  {CONFIDENCE_THRESHOLD}")
    print(f"  Cycle:       every {SLEEP_SECONDS // 60} min")
    print(f"  Markets:     top {MARKETS_PER_CYCLE} by liquidity")
    print(f"  Max close:   {MAX_DAYS_TO_CLOSE} days out")
    print(f"  Max open:    {MAX_OPEN_POSITIONS} positions")
    print(f"  Reserve:     {BANKROLL_RESERVE * 100:.0f}% cash floor")
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

    # 4a. Sort by closing soonest first — money comes back faster for reinvestment.
    #     Markets with no end_date go to the end.
    def closing_key(m):
        raw = m.get("end_date", "")
        if not raw:
            return datetime.max.replace(tzinfo=timezone.utc)
        try:
            # Gamma returns ISO strings like "2025-06-15T00:00:00Z"
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt
        except ValueError:
            return datetime.max.replace(tzinfo=timezone.utc)

    candidates.sort(key=closing_key)

    # 4b. Drop markets closing too far away — money locked up there = dead capital
    now = datetime.now(timezone.utc)

    def days_until_close(m):
        raw = m.get("end_date", "")
        if not raw:
            return 9999  # no end date = treat as far future
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return (dt - now).days
        except ValueError:
            return 9999

    before = len(candidates)
    candidates = [
        m for m in candidates
        if 0 <= days_until_close(m) <= MAX_DAYS_TO_CLOSE
    ]
    far_dropped = before - len(candidates)
    if far_dropped:
        print(f"  Dropped {far_dropped} markets (past end_date or > {MAX_DAYS_TO_CLOSE} days away)")

    if candidates:
        soonest = candidates[0].get("end_date", "unknown")
        print(f"  Prioritised by closing date — soonest: {soonest}")

    # 4c. AI analysis + paper trading
    bets_placed = 0
    for market in candidates:
        if market["liquidity"] < MIN_LIQUIDITY:
            continue

        # Cap total open positions — don't lock up all capital
        if len(ledger.get_open_condition_ids()) >= MAX_OPEN_POSITIONS:
            print(f"  ** Max open positions ({MAX_OPEN_POSITIONS}) reached — waiting for resolutions **")
            break

        # Keep a cash reserve — never bet below this floor
        reserve = ledger.data.get("starting_bankroll", BANKROLL) * BANKROLL_RESERVE
        if ledger.available_bankroll() <= reserve:
            print(f"  ** Bankroll at reserve floor (${reserve:.2f}) — holding cash **")
            break

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

        # Check bankroll (above reserve)
        if ledger.available_bankroll() - BET_AMOUNT < reserve:
            print("  ** Next bet would breach reserve — stopping **")
            break

        # Place paper trade
        direction = decision["direction"]
        entry_odds = (
            market["yes_price"] if direction == "YES" else market["no_price"]
        )
        token_id = (
            market["yes_token_id"] if direction == "YES" else market["no_token_id"]
        )

        # Kelly-scaled bet size (more confidence = bigger bet)
        bet_amount = kelly_bet(
            confidence=decision["confidence"],
            entry_odds=entry_odds,
            bankroll=ledger.available_bankroll(),
        )
        bet_amount = min(bet_amount, ledger.available_bankroll())

        ledger.place_trade(
            market=market,
            direction=direction,
            entry_odds=entry_odds,
            token_id=token_id,
            bet_amount=bet_amount,
        )
        bets_placed += 1

        print(
            f"  BET {direction:3s} {market['question'][:45]}  "
            f"@ {entry_odds:.3f}  conf={decision['confidence']:.2f}  size=${bet_amount:.2f}"
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
