"""
Two-step AI agent for prediction market analysis.

Step 1 — Analyst:
  Given market data + web research, estimate the true probability
  and identify the edge (where the crowd is wrong).

Step 2 — Critic + Final Decision:
  Challenges the analyst's reasoning, then outputs the final
  structured bet decision (or skip).

Using Groq's llama-3.3-70b-versatile (free, fast).
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


# ── Step 1: Analyst ──────────────────────────────────────────────────

ANALYST_SYSTEM = """\
You are a prediction market analyst. You estimate true probabilities and find
mispriced markets by combining base rates, research evidence, and market structure.

Given a market + web research, your job is to:
1. Estimate the TRUE probability of the YES outcome as a percentage
2. Compare it to the current market price
3. Identify whether there is a meaningful edge (≥5% gap)
4. Surface the key facts and risks

Be concrete. Ground every claim in the research provided.
If the research is sparse or contradictory, say so explicitly.

Respond ONLY with valid JSON:
{
  "true_probability": <float 0.0–1.0>,
  "edge_direction": "YES" or "NO" or "NONE",
  "edge_size": <float, estimated probability gap vs market price>,
  "key_facts": [<up to 3 short strings>],
  "main_risk": "<one sentence: what could make you wrong>",
  "reasoning": "<2–3 sentences of analysis>"
}
"""


def _call_analyst(market: dict, context: str) -> dict | None:
    yes_price = market["yes_price"]
    no_price = market["no_price"]

    prompt = (
        f"Market: {market['question']}\n"
        f"Description: {market['description'] or 'No description'}\n"
        f"End date: {market['end_date']}\n"
        f"\n"
        f"YES price: {yes_price:.3f} ({yes_price * 100:.1f}% implied probability)\n"
        f"NO price:  {no_price:.3f} ({no_price * 100:.1f}% implied probability)\n"
        f"Liquidity: ${market['liquidity']:,.0f}\n"
    )

    if context:
        prompt += f"\n{context}\n"

    prompt += "\nEstimate the true probability and identify the edge."

    try:
        resp = _get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": ANALYST_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=350,
        )
        raw = _strip_fences(resp.choices[0].message.content.strip())
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [ai/analyst] Bad JSON")
        return None
    except Exception as e:
        print(f"  [ai/analyst] Error: {e}")
        return None


# ── Step 2: Critic → Final Decision ─────────────────────────────────

CRITIC_SYSTEM = """\
You are a prediction market critic and final decision maker.

You will receive:
- A market with its current prices
- An analyst's probability estimate and reasoning
- The key facts and main risk the analyst identified

Your job:
1. Challenge the analyst — are they overconfident? Missing contrary evidence?
   Anchoring to one data point? Confusing correlation with causation?
2. Make the FINAL bet decision incorporating your critique.

Betting rules:
- Only bet if you can clearly articulate WHY the crowd price is wrong
- If the analyst's reasoning is weak or the evidence is thin, lower confidence or skip
- Confidence below 0.55 → set should_bet = false
- This is paper trading — it is OK to bet when edge is real

Respond ONLY with valid JSON:
{
  "critique": "<1–2 sentences: what the analyst might be wrong about>",
  "should_bet": <true or false>,
  "direction": "YES" or "NO",
  "confidence": <float 0.0–1.0>,
  "reasoning": "<1–2 sentences: final rationale for the bet or skip>"
}
"""


def _call_critic(market: dict, analyst: dict) -> dict | None:
    yes_price = market["yes_price"]
    no_price = market["no_price"]

    prompt = (
        f"Market: {market['question']}\n"
        f"YES price: {yes_price:.3f} | NO price: {no_price:.3f}\n"
        f"\n"
        f"=== Analyst output ===\n"
        f"True probability estimate: {analyst.get('true_probability', '?'):.1%}\n"
        f"Edge direction: {analyst.get('edge_direction', '?')}\n"
        f"Edge size: {analyst.get('edge_size', 0):.1%}\n"
        f"Key facts: {'; '.join(analyst.get('key_facts', []))}\n"
        f"Main risk: {analyst.get('main_risk', '')}\n"
        f"Reasoning: {analyst.get('reasoning', '')}\n"
        f"======================\n"
        f"\nChallenge this analysis. Then make the final bet decision."
    )

    try:
        resp = _get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": CRITIC_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=300,
        )
        raw = _strip_fences(resp.choices[0].message.content.strip())
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [ai/critic] Bad JSON")
        return None
    except Exception as e:
        print(f"  [ai/critic] Error: {e}")
        return None


# ── Public API ───────────────────────────────────────────────────────

def analyze_market(market: dict) -> dict | None:
    """
    Full two-step agent: research → analyst → critic → final decision.

    Returns dict with: should_bet, direction, confidence, reasoning
    Returns None if either API call fails.
    """
    # Research
    context = fetch_context(market["question"])
    if context:
        print(f"  [ai] Research found {context.count('•')} snippets")
    else:
        print(f"  [ai] No research results — proceeding on priors")

    # Step 1: Analyst
    analyst = _call_analyst(market, context)
    if analyst is None:
        return None

    edge = analyst.get("edge_direction", "NONE")
    prob = analyst.get("true_probability", 0)
    print(
        f"  [ai/analyst] true_prob={prob:.1%}  edge={edge}  "
        f"gap={analyst.get('edge_size', 0):.1%}"
    )

    # Fast-exit: if analyst sees no edge, skip the critic call (saves tokens)
    if edge == "NONE" or analyst.get("edge_size", 0) < 0.03:
        print(f"  [ai] Analyst found no edge — skipping critic")
        return {
            "should_bet": False,
            "direction": "YES",
            "confidence": 0.0,
            "reasoning": analyst.get("reasoning", "No edge identified"),
        }

    # Step 2: Critic + Final Decision
    critic = _call_critic(market, analyst)
    if critic is None:
        return None

    should_bet = bool(critic.get("should_bet", False))
    direction = critic.get("direction", "YES").upper()
    confidence = max(0.0, min(1.0, float(critic.get("confidence", 0))))
    reasoning = critic.get("reasoning", "No reasoning provided")
    critique = critic.get("critique", "")

    if not should_bet:
        confidence = 0.0

    print(f"  [ai/critic] {critique[:80]}")

    return {
        "should_bet": should_bet,
        "direction": direction if direction in ("YES", "NO") else "YES",
        "confidence": confidence,
        "reasoning": reasoning,
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """Remove markdown ```json ... ``` wrappers if the model adds them."""
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
        elif len(parts) == 2:
            text = parts[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()
