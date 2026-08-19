"""Celery application — the background task queue for Obsidian.

Redis is the broker (task queue) and result backend. This module is intentionally
lightweight and safe to import from anywhere (e.g. backend.py, to enqueue tasks): it
creates only the Celery instance and the Beat schedule — NO Flask app, NO db.init, NO
service init. The Flask app context that tasks run in is built lazily in tasks.py, so
importing this from the web process has no side effects.

Run the worker and the scheduler as separate processes:
    celery -A celery_app.celery worker --loglevel=info --pool=solo   # --pool=solo on Windows
    celery -A celery_app.celery beat   --loglevel=info
"""

import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
_WATCH_INTERVAL_MIN = float(os.environ.get('WATCH_INTERVAL_MINUTES', '30'))

celery = Celery(
    'obsidian',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks'],   # the worker imports tasks.py to register the task functions
)

# Beat fires this periodically; the task fans out one agent task per due watch.
celery.conf.beat_schedule = {
    'check-due-watches': {
        'task': 'tasks.check_due_watches',
        'schedule': _WATCH_INTERVAL_MIN * 60,   # seconds
    },
}
celery.conf.timezone = 'UTC'
