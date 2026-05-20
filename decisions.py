"""
CSV decision logger — records every AI analysis for later review.

Every market the AI looks at gets a row, whether it bets or not.
The "outcome" column stays empty until the market resolves.
"""

import csv
import os
import time

DECISIONS_FILE = "decisions.csv"

HEADERS = [
    "timestamp",
    "market_id",
    "market_title",
    "direction",
    "confidence",
    "implied_prob",
    "ai_reasoning",
    "outcome",
]


def log_decision(market, decision):
    """Append one row to decisions.csv."""
    file_exists = os.path.exists(DECISIONS_FILE)

    with open(DECISIONS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)

        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "market_id": market["condition_id"],
                "market_title": market["question"],
                "direction": decision.get("direction", ""),
                "confidence": round(decision.get("confidence", 0), 3),
                "implied_prob": round(market["yes_price"], 3),
                "ai_reasoning": decision.get("reasoning", ""),
                "outcome": "",  # Filled when market resolves
            }
        )
