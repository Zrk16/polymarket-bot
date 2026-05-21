"""
Groq AI decision maker.

Sends market data to llama3-70b via Groq's free API.
Returns structured bet recommendation (direction, confidence, reasoning).
"""

import json
import os

from groq import Groq
from search import fetch_context

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set in .env")
        _client = Groq(api_key=api_key)
    return _client


SYSTEM_PROMPT = """\
You are a prediction market trader. You will be given a Polymarket market
with its title, description, and current YES/NO prices.

Your job: estimate the true probability of this event and decide whether
to bet YES or NO based on where the market price is relative to your estimate.

Respond ONLY with valid JSON in this exact format:
{
  "should_bet": true or false,
  "direction": "YES" or "NO",
  "confidence": 0.0 to 1.0,
  "reasoning": "1-2 sentence explanation"
}

Guidelines:
- Use the web search results to inform your probability estimate
- Bet whenever your estimated probability diverges from the market price by 5% or more
- "confidence" = how confident you are in your probability estimate (0.55+ is fine)
- It is OK to bet — this is paper trading, not real money
- Pick the direction that makes money if your estimate is correct
- Only skip if you have zero knowledge of the topic even after reading the search results
"""


def analyze_market(market):
    """
    Ask Groq AI whether this market is mispriced and worth betting on.

    Returns dict with: should_bet, direction, confidence, reasoning
    Returns None if the API call fails.
    """
    yes_price = market["yes_price"]
    no_price = market["no_price"]

    # Fetch live web context so the AI knows what's actually happening
    web_context = fetch_context(market["question"])

    prompt = (
        f"Market: {market['question']}\n"
        f"Description: {market['description'] or 'No description available'}\n"
        f"End date: {market['end_date']}\n"
        f"\n"
        f"Current YES price: ${yes_price:.3f} ({yes_price * 100:.1f}% implied)\n"
        f"Current NO price:  ${no_price:.3f} ({no_price * 100:.1f}% implied)\n"
        f"Liquidity: ${market['liquidity']:,.0f}\n"
    )

    if web_context:
        prompt += f"\n{web_context}\n"

    prompt += "\nIs this market mispriced based on the latest information? Should I bet YES or NO?"

    try:
        response = _get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if the model wraps its JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)

        should_bet = bool(data.get("should_bet", False))
        direction = data.get("direction", "YES").upper()
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
        reasoning = data.get("reasoning", "No reasoning provided")

        if not should_bet:
            confidence = 0.0

        return {
            "should_bet": should_bet,
            "direction": direction if direction in ("YES", "NO") else "YES",
            "confidence": confidence,
            "reasoning": reasoning,
        }

    except json.JSONDecodeError:
        print(f"  [ai] Bad JSON from Groq: {raw[:120]}")
        return None
    except Exception as e:
        print(f"  [ai] Groq API error: {e}")
        return None
