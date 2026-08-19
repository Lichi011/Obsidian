"""Price-watch data access (database-backed).

Create/read/disable watches for signed-in users. Rows live in PostgreSQL (models.py) —
owned by a User. The actual price-checking is done by Celery: Beat fires
tasks.check_due_watches on a schedule, which fans out tasks.run_watch_agent per due watch
(see tasks.py and celery_app.py). This module holds only the CRUD used by the web layer
plus the serialization the tasks reuse.

DB access needs a Flask app context. Request handlers already have one; init_watch_service
gives us the app so the same functions work if called from a worker too.
"""

import os
import re
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import func

from models import db, User, Watch

load_dotenv()

# Fallback re-check cadence when the agent doesn't choose one; also the Beat sweep interval
# (see celery_app.py). The agent normally sets each watch's own next-check time.
CHECK_INTERVAL_SECONDS = int(os.environ.get('WATCH_INTERVAL_MINUTES', '30')) * 60

_app = None   # Flask app, set by init_watch_service (for app contexts)


def init_watch_service(app):
    """Give watch_service the Flask app so it can open app contexts (call once at startup)."""
    global _app
    _app = app


# ----------------------------------------------------------------- serialization

def _f(val):
    """Numeric/Decimal -> float (JSON-safe, and what the agent expects). None passes through."""
    return float(val) if val is not None else None


def _watch_to_dict(w):
    """Serialize a Watch row to the dict shape the frontend and agent expect."""
    return {
        'id': w.id,
        'product': w.product,
        'url': w.product_url or '',
        'email': w.user.email if w.user else '',
        'baseline_price': _f(w.baseline_price),
        'target_price': _f(w.target_price),
        'last_price': _f(w.last_price),
        'last_price_text': w.last_price_text or '',
        'enabled': w.enabled,
        'notified': w.notified,
        'next_check_at': w.next_check_at.isoformat() if w.next_check_at else None,
        'last_checked': w.last_checked.isoformat() if w.last_checked else None,
        'last_decision': w.last_decision or '',
        'last_error': w.last_error,
        'created_at': w.created_at.isoformat() if w.created_at else None,
    }


# ----------------------------------------------------------------- public API

def enable_watch(product, url='', email='', baseline_price=None, target_price=None):
    """Create (or re-arm) a watch for the (user, product) pair. Returns the watch dict."""
    product = (product or '').strip()
    email = (email or '').strip()
    if not product or not email:
        raise ValueError('Both a product and an email address are required.')

    baseline = _to_number(baseline_price)
    target = _to_number(target_price)

    with _app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(google_sub=f'email:{email}', email=email)
            db.session.add(user)
            db.session.flush()

        existing = (
            Watch.query
            .filter(Watch.user_id == user.id, func.lower(Watch.product) == product.lower())
            .first()
        )
        w = existing or Watch(user_id=user.id, product=product)
        w.product_url = (url or '').strip()
        if baseline is not None:
            w.baseline_price = baseline
        w.target_price = target
        w.enabled = True
        w.notified = False
        w.next_check_at = datetime.utcnow()   # due immediately on the next Beat sweep
        w.last_error = None
        if existing is None:
            db.session.add(w)
        db.session.commit()
        return _watch_to_dict(w)


def disable_watch(watch_id=None, product=None, email=None):
    """Turn off a watch by id, or by (product, email). Returns True if one was found.

    If `email` is given it must own the watch (stops disabling someone else's by id).
    """
    with _app.app_context():
        w = None
        if watch_id is not None:
            w = db.session.get(Watch, _as_int(watch_id))
        elif product and email:
            user = User.query.filter_by(email=email.strip()).first()
            if user:
                w = (
                    Watch.query
                    .filter(Watch.user_id == user.id,
                            func.lower(Watch.product) == product.strip().lower())
                    .first()
                )
        if not w:
            return False
        if email and (not w.user or w.user.email.lower() != email.strip().lower()):
            return False
        w.enabled = False
        db.session.commit()
        return True


def list_watches(email=None):
    """All watches, optionally scoped to one user's email. Returns a list of dicts."""
    with _app.app_context():
        q = Watch.query
        if email:
            user = User.query.filter_by(email=email.strip()).first()
            if not user:
                return []
            q = q.filter_by(user_id=user.id)
        return [_watch_to_dict(w) for w in q.all()]


# ----------------------------------------------------------------- internals

def _to_number(raw):
    if raw is None or raw == '':
        return None
    try:
        return float(re.sub(r'[^\d.]', '', str(raw)))
    except (TypeError, ValueError):
        return None


def _as_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return -1
