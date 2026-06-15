"""The price-watch AI AGENT (LangChain edition).

WHAT MAKES THIS AN AGENT (vs. the old watcher)
-----------------------------------------------
The old watcher was a fixed rule you wrote: `if price < baseline: send email`.

This module instead gives Gemini a GOAL and a set of TOOLS, then lets it decide, step by
step, what to do. The model can only *ask* to call a tool; our Python runs it and hands
the result back, and the model reacts. This "reason -> act -> observe" loop is the thing
that makes it an agent.

LANGCHAIN PORT
--------------
Previously this loop was hand-written. Here it's built with LangChain 1.x's `create_agent`
(a prebuilt, LangGraph-backed ReAct agent), so the loop, tool dispatch, and message
plumbing are handled for us. We only supply:
  1. TOOLS  - plain Python functions decorated with @tool (schema comes from type hints +
              docstrings). They're defined inside run_watch_agent so they close over the
              per-run `state` scratchpad — the same role the old hand-passed `state` had.
  2. BRIEF  - the agent's role/goal/rules, passed as the system prompt.
  3. MODEL  - ChatGoogleGenerativeAI (same Gemini model + 'low' thinking as before).

The grounded Google-Search price/alternative lookups still live inside gemini_service;
the tools just call those helpers, so LangChain never has to model the search tool itself.
"""

import json
import sys
from typing import Dict, Optional

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

try:
    from langgraph.errors import GraphRecursionError
except Exception:  # pragma: no cover - defensive: path could move across versions
    GraphRecursionError = RuntimeError

# Reuse the model config and the grounded-search helpers that already exist.
from gemini_service import GEMINI_API_KEY, MODEL, get_current_price, get_top_products
from email_service import send_email, email_configured

# Safety cap: bound how many reason->act rounds the agent may take (each costs an API
# call). LangGraph counts "supersteps" (~2 per tool round), so translate our step budget
# into a recursion limit with a little headroom.
MAX_STEPS = 8
RECURSION_LIMIT = 2 * MAX_STEPS + 2


def _log(msg):
    """Print without crashing on a console (e.g. Windows cp1252) that can't encode a
    character such as the ₹ symbol — fall back to replacing those characters."""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or 'utf-8'
        print(str(msg).encode(enc, 'replace').decode(enc))


def _message_text(message) -> str:
    """Extract plain text from a LangChain message.

    Gemini (via langchain-google-genai) may return `content` either as a plain string or
    as a list of content blocks (dicts like {'type': 'text', 'text': ...} and/or thinking
    blocks). Normalise both shapes to a single stripped string.
    """
    content = getattr(message, 'content', '') or ''
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get('text'):
                parts.append(block['text'])
        return ' '.join(parts).strip()
    return str(content).strip()


# The agent's role, goal, and rules. Filled per-watch and passed as the system prompt.
_AGENT_BRIEF = """\
You are a price-watch agent working on behalf of a shopper. Your job: decide whether to
notify them about their watched product, and when to check it next.

The shopper's watch:
- Product: {product}
- Product link: {url}
- Price when they started watching (baseline): {baseline}
- Their target price (notify at/under this), if any: {target}
- Most recent price you saw last time: {last_price}
- Have you already emailed them about a drop?: {already_notified}

How to work:
1. Call get_current_price first to see today's price.
2. Reason about it:
   - If there is a target: a notify is warranted when the price is at or below it.
   - If there is no target: notify on a MEANINGFUL drop below the baseline (ignore tiny
     noise like a few rupees). Use judgement.
   - Do NOT email again for a drop you've already reported, unless the price has dropped
     further. If the price went back up, there's nothing to report.
   - Optionally use find_alternatives if it genuinely helps the shopper.
3. If (and only if) a notification is warranted, call notify_user with a clear subject
   and a short, friendly body that includes the old price, new price, and the buy link.
4. Call set_next_check_hours to choose when to look again (sooner if volatile or close to
   target; later if stable).
5. Finish by replying with ONE short sentence summarizing what you did and why.
"""


def _build_tools(state: Dict):
    """Create the agent's tools bound to THIS run's `state` scratchpad.

    Defining them as closures (instead of passing `state` around) lets each tool record
    what it observed/did so the scheduler can persist it after the run finishes.
    """

    @tool
    def get_current_price_tool(product: str, url: str = '') -> dict:
        """Look up the product's CURRENT price online (Amazon/Flipkart). Call this first
        to see today's price before deciding anything. `url` is an optional known link."""
        try:
            quote = get_current_price(product, url)
        except Exception as exc:
            return {'error': f'price lookup failed: {exc}'}
        if quote.get('price_value') is not None:
            state['observed_price'] = quote['price_value']
            state['observed_price_text'] = quote.get('price_text', '')
        return quote

    @tool
    def find_alternatives(query: str, max_price: Optional[float] = None) -> dict:
        """Search for similar products, optionally cheaper than a price cap. Use only if it
        would genuinely help the user (e.g. price went UP, or a cheaper option is worth
        flagging). `query` e.g. "noise cancelling headphones under 15000"."""
        try:
            results = get_top_products(query)
        except Exception as exc:
            return {'error': f'alternative search failed: {exc}'}
        out = [{
            'name': p.get('name', ''),
            'price': p.get('price', ''),
            'source': p.get('source', ''),
            'link': p.get('purchase_link', ''),
        } for p in results[:6]]
        return {'alternatives': out}

    @tool
    def notify_user(subject: str, body: str) -> dict:
        """Email the user. Call ONLY when there is something genuinely worth telling them
        (a meaningful price drop, or it reached their target). You write the subject and
        body yourself — be concise and helpful, and include the prices and the buy link."""
        # Backstop against spam/cost: at most one email per agent run.
        if state.get('email_sent'):
            return {'sent': False, 'reason': 'an email was already sent in this run'}
        sent = send_email(state['email'], subject, body)
        state['email_sent'] = sent
        if sent:
            state['notified'] = True
        return {'sent': sent}

    @tool
    def set_next_check_hours(hours: float, reason: str = '') -> dict:
        """Decide how many hours from now you should re-check this product. Sooner if the
        price is volatile or near the target; later if stable and far from it. Call this
        once before finishing."""
        hours = max(0.1, float(hours))  # never schedule a check in the past / instantly
        state['next_check_hours'] = hours
        state['cadence_reason'] = reason
        return {'ok': True, 'next_check_in_hours': hours}

    return [get_current_price_tool, find_alternatives, notify_user, set_next_check_hours]


def run_watch_agent(watch: Dict) -> Dict:
    """Run one full agent turn for a single watch.

    Returns a dict of fields for the scheduler to save back onto the watch:
    {last_price, last_price_text, notified, next_check_hours, last_decision, last_error}.
    """
    # `state` is the agent's scratchpad for THIS run. Tools read/write it; we read it at
    # the end to learn what the agent actually did.
    state = {
        'email': watch['email'],
        'notified': watch.get('notified', False),
        'email_sent': False,
        'observed_price': None,
        'observed_price_text': '',
        'next_check_hours': None,
        'cadence_reason': '',
    }

    brief = _AGENT_BRIEF.format(
        product=watch['product'],
        url=watch.get('url') or '(none)',
        baseline=watch.get('baseline_price'),
        target=watch.get('target_price') if watch.get('target_price') is not None else '(none)',
        last_price=watch.get('last_price') if watch.get('last_price') is not None else '(first check)',
        already_notified=watch.get('notified', False),
    )

    llm = ChatGoogleGenerativeAI(
        model=MODEL,
        google_api_key=GEMINI_API_KEY,
        thinking_level='low',  # same low-latency reasoning budget as the original
    )

    # `create_agent` builds the prebuilt ReAct loop; we hand it the model, tools, and the
    # brief as the system prompt. A fresh agent per run keeps the tool closures bound to
    # this run's `state` (watch runs are infrequent, so rebuilding the graph is cheap).
    agent = create_agent(model=llm, tools=_build_tools(state), system_prompt=brief)

    final_text = ''
    last_error = None
    try:
        result = agent.invoke(
            {'messages': [HumanMessage(content='Run the watch check now.')]},
            config={'recursion_limit': RECURSION_LIMIT},
        )
        messages = result.get('messages', []) if isinstance(result, dict) else []
        if messages:
            final_text = _message_text(messages[-1])
        _log(f'[agent] finished — {final_text}')
    except GraphRecursionError:
        last_error = f'agent stopped after ~{MAX_STEPS} steps without concluding'
        _log(f'[agent] {last_error}')
    except Exception as exc:
        last_error = f'agent run failed: {exc}'
        _log(f'[agent] {last_error}')

    return {
        'last_price': state['observed_price'],
        'last_price_text': state['observed_price_text'],
        'notified': state['notified'],
        'next_check_hours': state['next_check_hours'],
        'last_decision': final_text,
        'last_error': last_error,
    }


# --- Manual test: run one agent turn against a fake watch ----------------------
if __name__ == '__main__':
    demo_watch = {
        'product': 'Sony WH-1000XM5',
        'url': '',
        'email': 'test@example.com',
        'baseline_price': 29990,
        'target_price': 24000,
        'last_price': None,
        'notified': False,
    }
    print('email configured:', email_configured())
    print(json.dumps(run_watch_agent(demo_watch), indent=2))
