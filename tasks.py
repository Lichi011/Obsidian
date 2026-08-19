"""Celery tasks — the background work (ingestion, price-watch agent), run by the worker.

Enqueue from the web process with `ingest_task.delay(...)` etc. (that just pushes a message
to Redis — no Flask app needed). The WORKER runs the task bodies, and each runs inside a
Flask app context so SQLAlchemy works. That worker app is built lazily (first task run),
so importing this module from backend.py to enqueue has no side effects.
"""

import os
from datetime import datetime, timedelta

from celery_app import celery
from models import db, Watch, IngestionJob, PriceHistory
import product_knowledge as pk
import watch_service as ws
from agent_service import run_watch_agent

_worker_app = None


def _get_worker_app():
    """Build (once) a Flask app for the worker so tasks have a DB context."""
    global _worker_app
    if _worker_app is None:
        from flask import Flask
        app = Flask('obsidian-worker')
        url = os.environ.get('DATABASE_URL', 'sqlite:///obsidian.db')
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = url
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        db.init_app(app)
        pk.init_product_knowledge(app)   # quota persistence
        ws.init_watch_service(app)       # serialization helpers
        _worker_app = app
    return _worker_app


class ContextTask(celery.Task):
    """Run every task body inside the worker's Flask app context."""
    def __call__(self, *args, **kwargs):
        with _get_worker_app().app_context():
            return self.run(*args, **kwargs)


celery.Task = ContextTask


# ------------------------------------------------------------------ ingestion

def _set_job(product, **fields):
    """Upsert the IngestionJob for a product (runs inside the task's app context)."""
    job = (IngestionJob.query.filter_by(product_slug=product)
           .order_by(IngestionJob.id.desc()).first())
    if job is None:
        job = IngestionJob(product_slug=product, status=fields.get('status', 'preparing'))
        db.session.add(job)
    if 'chunks' in fields:
        job.chunk_count = fields.pop('chunks')
    for key, val in fields.items():
        if hasattr(job, key):
            setattr(job, key, val)
    db.session.commit()


@celery.task(name='tasks.ingest')
def ingest_task(product):
    """Scrape Reddit -> chunk -> embed for a product, updating IngestionJob as it goes."""
    def on_progress(stage, message):
        _set_job(product, status='preparing', stage=stage, message=message)

    try:
        count = pk.ingest(product, on_progress=on_progress)
        _set_job(product, status='ready', stage='ready',
                 message=f'Ready — learned from {count} Reddit opinions.', chunks=count)
    except Exception as exc:
        _set_job(product, status='error', stage='error', message=str(exc))


# ------------------------------------------------------------------ price watch

@celery.task(name='tasks.check_due_watches')
def check_due_watches():
    """Beat fires this on a schedule: claim due watches and fan out one agent task each.

    `FOR UPDATE SKIP LOCKED` lets multiple workers claim disjoint batches with no
    coordination and no double-processing. We push next_check_at forward as a provisional
    claim window; the agent task overwrites it with its real decision.
    """
    now = datetime.utcnow()
    due = (
        db.session.query(Watch)
        .filter(Watch.enabled.is_(True),
                (Watch.next_check_at.is_(None)) | (Watch.next_check_at <= now))
        .with_for_update(skip_locked=True)
        .all()
    )
    ids = [w.id for w in due]
    provisional = now + timedelta(seconds=ws.CHECK_INTERVAL_SECONDS)
    for w in due:
        w.next_check_at = provisional
    db.session.commit()

    for watch_id in ids:
        run_watch_agent_task.delay(watch_id)
    return {'claimed': ids}


@celery.task(name='tasks.run_watch_agent')
def run_watch_agent_task(watch_id):
    """Run the AI agent for one watch and persist its decision (+ log the price)."""
    w = db.session.get(Watch, watch_id)
    if w is None:
        return
    snapshot = ws._watch_to_dict(w)
    result = run_watch_agent(snapshot)

    next_hours = result.get('next_check_hours')
    delta = timedelta(hours=next_hours) if next_hours else timedelta(seconds=ws.CHECK_INTERVAL_SECONDS)
    w.last_checked = datetime.utcnow()
    w.next_check_at = datetime.utcnow() + delta
    w.notified = result.get('notified', w.notified)
    w.last_decision = result.get('last_decision', '')
    w.last_error = result.get('last_error')
    if result.get('last_price') is not None:
        w.last_price = result['last_price']
        w.last_price_text = result.get('last_price_text', '')
        db.session.add(PriceHistory(watch_id=w.id, price=result['last_price']))
    db.session.commit()
