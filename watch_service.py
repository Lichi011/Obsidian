"""Price-watch agent for the storefront.

When a user enables "watch mode" on a product, we record a watch (product name,
purchase link, their email, and the price at that moment as the baseline). A single
background thread periodically re-checks each active watch's live price via
gemini_service.get_current_price (grounded Google Search) and emails the user when the
price drops.

Alert rule:
    - If the watch has a target price: alert when the live price is <= target.
    - Otherwise: alert on any drop below the baseline (the price when the watch was set).
After alerting we mark the watch "notified" so it won't spam; if the price later climbs
back above the baseline, the watch re-arms so a future drop alerts again.

State is kept in memory and mirrored to .watches.json so watches survive a restart
(matching the .quota.json approach in product_knowledge.py). No external dependencies:
email goes out over stdlib smtplib via Gmail using an app password from .env.

Honest caveat: grounding-sourced prices are approximate and may lag the live listing.
Swap get_current_price for a real price API later without touching this file's logic.
"""

import json
import os
import re
import threading
import time
import uuid

from dotenv import load_dotenv

from agent_service import run_watch_agent

load_dotenv()

# How often the background thread WAKES UP to look for watches that are due. The agent
# decides each watch's own next-check time; this is just the polling granularity and the
# fallback cadence when the agent doesn't specify one.
CHECK_INTERVAL_SECONDS = int(os.environ.get('WATCH_INTERVAL_MINUTES', '30')) * 60

_WATCHES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.watches.json')

_watches = {}                 # watch_id -> watch dict
_watches_lock = threading.Lock()
_scheduler_started = False
_scheduler_lock = threading.Lock()


# ----------------------------------------------------------------- persistence

def _load_watches():
    try:
        with open(_WATCHES_FILE) as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            _watches.update(saved)
    except Exception:
        pass


def _save_watches():
    try:
        with open(_WATCHES_FILE, 'w') as f:
            json.dump(_watches, f, indent=2)
    except Exception:
        pass


_load_watches()


# ----------------------------------------------------------------- public API

def enable_watch(product, url='', email='', baseline_price=None, target_price=None):
    """Create (or re-arm) a watch for a product+email pair. Returns the watch dict.

    `baseline_price` is the price the user is watching from (usually the price shown on
    the card). If omitted, the first scheduler check establishes it.
    """
    product = (product or '').strip()
    email = (email or '').strip()
    if not product or not email:
        raise ValueError('Both a product and an email address are required.')

    baseline = _to_number(baseline_price)
    target = _to_number(target_price)

    with _watches_lock:
        # One active watch per (product, email): reuse it instead of duplicating.
        existing = next(
            (w for w in _watches.values()
             if w['product'].lower() == product.lower()
             and w['email'].lower() == email.lower()),
            None,
        )
        watch = existing or {'id': uuid.uuid4().hex[:12], 'created_at': time.time()}
        watch.update({
            'product': product,
            'url': (url or '').strip(),
            'email': email,
            'baseline_price': baseline if baseline is not None else watch.get('baseline_price'),
            'target_price': target,
            'enabled': True,
            'notified': False,
            'last_price': watch.get('last_price'),
            'last_price_text': watch.get('last_price_text', ''),
            'last_checked': watch.get('last_checked'),
            'last_decision': '',          # the agent's one-line summary of its last run
            'next_check_at': 0,           # 0 = due immediately on the next sweep
            'last_error': None,
        })
        _watches[watch['id']] = watch
        _save_watches()
        return dict(watch)


def disable_watch(watch_id=None, product=None, email=None):
    """Turn off a watch by id, or by (product, email). Returns True if one was found."""
    with _watches_lock:
        target_id = watch_id
        if not target_id and product and email:
            target_id = next(
                (wid for wid, w in _watches.items()
                 if w['product'].lower() == product.strip().lower()
                 and w['email'].lower() == email.strip().lower()),
                None,
            )
        watch = _watches.get(target_id) if target_id else None
        if not watch:
            return False
        watch['enabled'] = False
        _save_watches()
        return True


def list_watches(email=None):
    """All watches, optionally filtered to one email. Returns a list of copies."""
    with _watches_lock:
        items = list(_watches.values())
    if email:
        email = email.strip().lower()
        items = [w for w in items if w['email'].lower() == email]
    return [dict(w) for w in items]


def start_scheduler():
    """Start the single background sweep thread (idempotent)."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    print(f'[watch_service] scheduler started (every {CHECK_INTERVAL_SECONDS // 60} min)')


# ----------------------------------------------------------------- internals

def _to_number(raw):
    if raw is None or raw == '':
        return None
    try:
        return float(re.sub(r'[^\d.]', '', str(raw)))
    except (TypeError, ValueError):
        return None


def _scheduler_loop():
    while True:
        try:
            _sweep_once()
        except Exception as exc:  # never let one bad sweep kill the thread
            print(f'[watch_service] sweep error: {exc}')
        time.sleep(CHECK_INTERVAL_SECONDS)


def _sweep_once():
    """Run the AI agent for every enabled watch that is due for a check.

    The scheduler no longer decides anything about prices — it only picks which watches
    are due and hands each one to the agent (agent_service.run_watch_agent). The agent
    looks up the price, decides whether to email, and chooses when to be checked next.
    """
    now = time.time()
    with _watches_lock:
        due = [dict(w) for w in _watches.values()
               if w.get('enabled') and w.get('next_check_at', 0) <= now]
    for snapshot in due:
        _run_agent_for(snapshot)


def _run_agent_for(snapshot):
    """Run the agent on one watch snapshot and save what it decided. Returns the result."""
    result = run_watch_agent(snapshot)  # <-- the agent does the thinking here

    # The agent told us how many hours until the next check; fall back to the default
    # sweep interval if it didn't decide.
    next_hours = result.get('next_check_hours')
    next_at = time.time() + (next_hours * 3600 if next_hours else CHECK_INTERVAL_SECONDS)

    fields = {
        'last_checked': time.time(),
        'next_check_at': next_at,
        'notified': result.get('notified', snapshot.get('notified', False)),
        'last_decision': result.get('last_decision', ''),
        'last_error': result.get('last_error'),
    }
    # Only overwrite the last seen price if the agent actually observed one.
    if result.get('last_price') is not None:
        fields['last_price'] = result['last_price']
        fields['last_price_text'] = result.get('last_price_text', '')

    _update_watch(snapshot['id'], **fields)
    return result


def run_now(watch_id=None):
    """Force an immediate agent run (ignoring the schedule), for testing.

    Runs one watch by id, or all enabled watches if no id is given. Returns a list of
    {id, product, ...agent result}.
    """
    with _watches_lock:
        targets = [dict(w) for w in _watches.values()
                   if w.get('enabled') and (watch_id is None or w['id'] == watch_id)]
    out = []
    for snap in targets:
        result = _run_agent_for(snap)
        out.append({'id': snap['id'], 'product': snap['product'], **result})
    return out


def _update_watch(watch_id, **fields):
    with _watches_lock:
        watch = _watches.get(watch_id)
        if not watch:
            return
        watch.update(fields)
        _save_watches()
