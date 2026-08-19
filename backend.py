import os
from flask import Flask, jsonify, request, redirect, url_for, make_response, send_from_directory
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from gemini_service import get_top_products, answer_product_question
import product_knowledge as pk
import watch_service as watch
from models import db, User, IngestionJob
from auth_service import (
    init_auth, google_client, google_configured, mint_jwt, current_user,
    require_auth, set_session_cookie, clear_session_cookie,
)

# Serve the built React storefront (storefront-ui). Run `npm run build` in
# storefront-ui/ after UI changes to refresh what Flask serves here.
DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'storefront-ui', 'dist')

app = Flask(__name__, static_folder=DIST_DIR, static_url_path='')
# Flask session cookie signing (used by Authlib to hold the OAuth state during the
# Google redirect). Must be stable across restarts/workers.
app.secret_key = os.environ.get('SECRET_KEY', 'dev-insecure-change-me')
# Secure cookies require HTTPS; disable in local dev (http) so the cookie still sets.
_COOKIE_SECURE = os.environ.get('FLASK_DEBUG', '1') != '1'
init_auth(app)

# --- Database (SQLAlchemy + Flask-Migrate) -------------------------------------
# DATABASE_URL selects the backend: Postgres in prod, SQLite by default locally.
# (Some hosts hand out legacy postgres:// URLs — SQLAlchemy needs postgresql://.)
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///obsidian.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
migrate = Migrate(app, db)
watch.init_watch_service(app)   # give the scheduler/watch layer the app for DB contexts
pk.init_product_knowledge(app)  # give product_knowledge the app so quota persists to the DB

# --- Rate limiting (Flask-Limiter, Redis-backed) -------------------------------
# Keyed per logged-in user (falls back to IP). Counters live in Redis (REDIS_URL) — a
# real Redis is required to run the app; there is no in-memory fallback by design.
def _rate_key():
    user = current_user()
    return f"user:{user['sub']}" if user and user.get('sub') else get_remote_address()

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
limiter = Limiter(
    key_func=_rate_key,
    storage_uri=REDIS_URL,
    strategy='fixed-window',
)
limiter.init_app(app)

# --- "Talk to your product" ingestion state -------------------------------------
# Ingestion job status lives in the IngestionJob table (keyed by product name), so it
# survives restarts and is shareable across processes. The background ingestion thread
# has no request context, so DB access is wrapped in an app context.

def _set_job(product, **fields):
    """Upsert the ingestion job for a product. `chunks` maps to the chunk_count column."""
    with app.app_context():
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


def _get_job(product):
    """Latest ingestion job for a product as a dict, or None."""
    with app.app_context():
        job = (IngestionJob.query.filter_by(product_slug=product)
               .order_by(IngestionJob.id.desc()).first())
        if job is None:
            return None
        return {
            'status': job.status,
            'stage': job.stage,
            'message': job.message,
            'chunks': job.chunk_count or 0,
        }


@app.route('/')
def index():
    return send_from_directory(DIST_DIR, 'index.html')


@app.route('/search', methods=['POST'])
@limiter.limit('20 per hour')     # Gemini grounded search — costs API calls
@require_auth
def search():
    payload = request.get_json(silent=True) or {}
    description = payload.get('description', '').strip()
    if not description:
        return jsonify({'error': 'Description is required.'}), 400

    try:
        results = get_top_products(description)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    return jsonify({'results': results})


@app.route('/talk/prepare', methods=['POST'])
@limiter.limit('10 per hour')     # each prepare can spend up to ~9 RapidAPI requests
@require_auth
def talk_prepare():
    """Kick off Reddit ingestion for a product in the background."""
    payload = request.get_json(silent=True) or {}
    product = payload.get('product', '').strip()
    if not product:
        return jsonify({'error': 'Product is required.'}), 400

    # Set an initial status synchronously (so the first poll sees it), then enqueue the
    # slow scrape/embed as a Celery task for a worker to run in the background.
    _set_job(product, status='preparing', stage='starting',
             message='Getting ready...', chunks=0)
    from tasks import ingest_task
    ingest_task.delay(product)
    return jsonify({'status': 'preparing'})


@app.route('/talk/status', methods=['GET'])
@require_auth
def talk_status():
    product = request.args.get('product', '').strip()
    job = _get_job(product)
    if not job:
        return jsonify({'status': 'unknown'})
    return jsonify({**job, 'quota': pk.get_quota()})


@app.route('/talk/quota', methods=['GET'])
@require_auth
def talk_quota():
    """Remaining RapidAPI requests on the reddit3 free plan, for the UI meter."""
    return jsonify(pk.get_quota())


@app.route('/talk/ask', methods=['POST'])
@require_auth
def talk_ask():
    payload = request.get_json(silent=True) or {}
    product = payload.get('product', '').strip()
    question = payload.get('question', '').strip()
    history = payload.get('history', [])
    if not product or not question:
        return jsonify({'error': 'Product and question are required.'}), 400

    job = _get_job(product)
    if not job or job.get('status') != 'ready':
        return jsonify({'error': 'This product is not ready yet. Please wait a moment.'}), 409

    try:
        chunks = pk.retrieve(product, question)
        answer = answer_product_question(product, question, chunks, history)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    sources = sorted({c['source'] for c in chunks if c.get('source')})
    return jsonify({'answer': answer, 'sources': sources})


# --- Auth (Google OAuth + JWT session) -----------------------------------------

@app.route('/auth/login')
def auth_login():
    """Kick off the Google OAuth flow (full-page redirect to Google)."""
    if not google_configured():
        return jsonify({'error': 'Google login is not configured on the server.'}), 503
    redirect_uri = url_for('auth_callback', _external=True)
    return google_client().authorize_redirect(redirect_uri)


@app.route('/auth/callback')
def auth_callback():
    """Google redirects here with the code; exchange it, mint our JWT, set the cookie."""
    if not google_configured():
        return jsonify({'error': 'Google login is not configured on the server.'}), 503
    try:
        token = google_client().authorize_access_token()
    except Exception as exc:
        return jsonify({'error': f'Google sign-in failed: {exc}'}), 400
    info = token.get('userinfo') or {}
    sub, email = info.get('sub'), info.get('email')
    if not sub or not email:
        return jsonify({'error': 'Google did not return a verified email.'}), 400

    # Upsert the user row (identity keyed on Google's stable `sub`).
    user = User.query.filter_by(google_sub=sub).first()
    if user is None:
        user = User(google_sub=sub, email=email, name=info.get('name', ''))
        db.session.add(user)
    else:
        user.email = email
        user.name = info.get('name', '')
    db.session.commit()

    session_jwt = mint_jwt(sub, email, info.get('name', ''))
    resp = make_response(redirect('/'))
    return set_session_cookie(resp, session_jwt, _COOKIE_SECURE)


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    resp = make_response(jsonify({'status': 'logged_out'}))
    return clear_session_cookie(resp)


@app.route('/auth/me')
def auth_me():
    """Who is logged in, for the UI. Never 401s — returns {authenticated: false} if not."""
    user = current_user()
    if not user:
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True, 'email': user.get('email'), 'name': user.get('name', '')})


# --- Price watch (requires a logged-in user) -----------------------------------

@app.route('/watch/enable', methods=['POST'])
@limiter.limit('30 per hour')
@require_auth
def watch_enable():
    """Start watching a product's price for the logged-in user. Returns the watch."""
    payload = request.get_json(silent=True) or {}
    try:
        result = watch.enable_watch(
            product=payload.get('product', ''),
            url=payload.get('url', ''),
            email=current_user()['email'],   # from the session, never client-supplied
            baseline_price=payload.get('baseline_price'),
            target_price=payload.get('target_price'),
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'watch': result})


@app.route('/watch/disable', methods=['POST'])
@require_auth
def watch_disable():
    payload = request.get_json(silent=True) or {}
    # Scope to the caller's own email so one user can't disable another's watch by id.
    ok = watch.disable_watch(
        watch_id=payload.get('id'),
        product=payload.get('product'),
        email=current_user()['email'],
    )
    if not ok:
        return jsonify({'error': 'No matching watch found.'}), 404
    return jsonify({'status': 'disabled'})


@app.route('/watch/list', methods=['GET'])
@require_auth
def watch_list():
    """Only the logged-in user's watches — the email comes from the session, not a param."""
    return jsonify({'watches': watch.list_watches(email=current_user()['email'])})


@app.route('/watch/run-now', methods=['POST'])
@require_auth
def watch_run_now():
    """Enqueue an immediate agent run for the caller's watches (testing aid). Optional body:
    {"id": <watch id>}. With no id, enqueues every enabled watch the caller owns."""
    from tasks import run_watch_agent_task
    payload = request.get_json(silent=True) or {}
    wid = payload.get('id')
    watches = watch.list_watches(email=current_user()['email'])
    targets = [w for w in watches if w['enabled'] and (wid is None or w['id'] == wid)]
    for w in targets:
        run_watch_agent_task.delay(w['id'])
    return jsonify({'enqueued': [w['id'] for w in targets]})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    # Price checks are driven by Celery Beat (see celery_app.py), run as a separate
    # process — no in-process scheduler thread here anymore.
    app.run(host='0.0.0.0', port=port, debug=debug)
