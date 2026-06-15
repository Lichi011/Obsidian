import os
import threading
from flask import Flask, jsonify, request, send_from_directory
from gemini_service import get_top_products, answer_product_question
import product_knowledge as pk
import watch_service as watch

# Serve the built React storefront (storefront-ui). Run `npm run build` in
# storefront-ui/ after UI changes to refresh what Flask serves here.
DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'storefront-ui', 'dist')

app = Flask(__name__, static_folder=DIST_DIR, static_url_path='')

# --- "Talk to your product" ingestion state -------------------------------------
# In-memory registry of ingestion jobs, keyed by product name. No persistence by
# design (v1): every "Talk to product" click re-scrapes fresh. The GIL makes simple
# dict reads/writes safe enough for this prototype.
_jobs = {}
_jobs_lock = threading.Lock()


def _set_job(product, **fields):
    with _jobs_lock:
        job = _jobs.setdefault(product, {})
        job.update(fields)


def _ingest_worker(product):
    """Background ingestion: scrape Reddit -> chunk -> embed. Updates job status."""
    def on_progress(stage, message):
        _set_job(product, status='preparing', stage=stage, message=message)

    try:
        count = pk.ingest(product, on_progress=on_progress)
        _set_job(product, status='ready', stage='ready',
                 message=f'Ready — learned from {count} Reddit opinions.', chunks=count)
    except Exception as exc:
        _set_job(product, status='error', stage='error', message=str(exc))


@app.route('/')
def index():
    return send_from_directory(DIST_DIR, 'index.html')


@app.route('/search', methods=['POST'])
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
def talk_prepare():
    """Kick off Reddit ingestion for a product in the background."""
    payload = request.get_json(silent=True) or {}
    product = payload.get('product', '').strip()
    if not product:
        return jsonify({'error': 'Product is required.'}), 400

    # Always start fresh (no caching in v1).
    _set_job(product, status='preparing', stage='starting',
             message='Getting ready...', chunks=0)
    threading.Thread(target=_ingest_worker, args=(product,), daemon=True).start()
    return jsonify({'status': 'preparing'})


@app.route('/talk/status', methods=['GET'])
def talk_status():
    product = request.args.get('product', '').strip()
    with _jobs_lock:
        job = _jobs.get(product)
    if not job:
        return jsonify({'status': 'unknown'})
    return jsonify({**job, 'quota': pk.get_quota()})


@app.route('/talk/quota', methods=['GET'])
def talk_quota():
    """Remaining RapidAPI requests on the reddit3 free plan, for the UI meter."""
    return jsonify(pk.get_quota())


@app.route('/talk/ask', methods=['POST'])
def talk_ask():
    payload = request.get_json(silent=True) or {}
    product = payload.get('product', '').strip()
    question = payload.get('question', '').strip()
    history = payload.get('history', [])
    if not product or not question:
        return jsonify({'error': 'Product and question are required.'}), 400

    with _jobs_lock:
        job = _jobs.get(product)
    if not job or job.get('status') != 'ready':
        return jsonify({'error': 'This product is not ready yet. Please wait a moment.'}), 409

    try:
        chunks = pk.retrieve(product, question)
        answer = answer_product_question(product, question, chunks, history)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    sources = sorted({c['source'] for c in chunks if c.get('source')})
    return jsonify({'answer': answer, 'sources': sources})


# --- Price watch ---------------------------------------------------------------

@app.route('/watch/enable', methods=['POST'])
def watch_enable():
    """Start watching a product's price for a given email. Returns the watch."""
    payload = request.get_json(silent=True) or {}
    try:
        result = watch.enable_watch(
            product=payload.get('product', ''),
            url=payload.get('url', ''),
            email=payload.get('email', ''),
            baseline_price=payload.get('baseline_price'),
            target_price=payload.get('target_price'),
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'watch': result})


@app.route('/watch/disable', methods=['POST'])
def watch_disable():
    payload = request.get_json(silent=True) or {}
    ok = watch.disable_watch(
        watch_id=payload.get('id'),
        product=payload.get('product'),
        email=payload.get('email'),
    )
    if not ok:
        return jsonify({'error': 'No matching watch found.'}), 404
    return jsonify({'status': 'disabled'})


@app.route('/watch/list', methods=['GET'])
def watch_list():
    return jsonify({'watches': watch.list_watches(email=request.args.get('email'))})


@app.route('/watch/run-now', methods=['POST'])
def watch_run_now():
    """Trigger the agent immediately (testing aid). Optional body: {"id": "<watch id>"}.
    With no id, runs every enabled watch."""
    payload = request.get_json(silent=True) or {}
    results = watch.run_now(watch_id=payload.get('id'))
    return jsonify({'ran': results})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    # Start the background price-check sweep. With the debug reloader the script runs in
    # two processes; only the worker child sets WERKZEUG_RUN_MAIN, so we start there to
    # avoid two schedulers sending duplicate emails. (Under a WSGI server like gunicorn,
    # call watch.start_scheduler() from your app factory instead.)
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        watch.start_scheduler()
    app.run(host='0.0.0.0', port=port, debug=debug)
