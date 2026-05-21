"""
Web server — serves live dashboard + runs bot loop in background thread.

Routes:
  GET /            → dashboard page
  GET /api/ledger  → full ledger JSON
  GET /api/status  → portfolio summary
"""

import csv
import json
import os
import threading
import time

from flask import Flask, jsonify, render_template

from bot import run_cycle, SLEEP_SECONDS, BANKROLL
from ledger import Ledger, LEDGER_FILE
from decisions import DECISIONS_FILE

app = Flask(__name__)

ledger = None
bot_state = {
    "cycle": 0,
    "last_run": None,
    "running": False,
}


def bot_loop():
    global ledger
    ledger = Ledger(starting_bankroll=BANKROLL)
    bot_state["running"] = True

    while True:
        bot_state["cycle"] += 1
        bot_state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            run_cycle(ledger, bot_state["cycle"])
        except Exception as e:
            print(f"  [error] Cycle {bot_state['cycle']} failed: {e}")

        time.sleep(SLEEP_SECONDS)


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/ledger")
def api_ledger():
    if not os.path.exists(LEDGER_FILE):
        return jsonify({"trades": [], "bankroll": BANKROLL})
    with open(LEDGER_FILE, "r") as f:
        return jsonify(json.load(f))


@app.route("/api/status")
def api_status():
    if ledger is None:
        return jsonify({
            "bankroll": BANKROLL,
            "starting_bankroll": BANKROLL,
            "total_value": BANKROLL,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "open_count": 0,
            "resolved_count": 0,
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
        })
    return jsonify(ledger.get_summary())


@app.route("/api/decisions")
def api_decisions():
    if not os.path.exists(DECISIONS_FILE):
        return jsonify([])
    rows = []
    with open(DECISIONS_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return jsonify(rows[-50:])


@app.route("/api/bot")
def api_bot():
    return jsonify(bot_state)


if __name__ == "__main__":
    if not os.getenv("GROQ_API_KEY"):
        print("WARNING: GROQ_API_KEY not set — bot loop disabled, dashboard-only mode")
    else:
        thread = threading.Thread(target=bot_loop, daemon=True)
        thread.start()

    port = int(os.getenv("PORT", "7860"))
    app.run(host="0.0.0.0", port=port)
