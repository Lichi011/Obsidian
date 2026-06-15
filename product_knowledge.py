"""Reddit-backed product knowledge for the "Talk to your product" feature.

Pipeline (all free / local except nothing — no LLM is involved here):
    1. Scrape: top 30 Reddit posts about the product via Reddit's public JSON API.
    2. Chunk:  rule-based — one chunk per post body + one per top comment (5/post).
    3. Store:  ChromaDB in-memory collection; its default embedding function runs
               all-MiniLM-L6-v2 locally on CPU (downloads ~80 MB once, then offline).
    4. Retrieve: embed the user's question, return the most relevant chunks.

Reuse over re-scrape: before scraping, we check whether this product's collection is
already in the (persistent) vector DB and still fresh (within CACHE_TTL_DAYS). If so we
reuse it and spend zero RapidAPI requests; only missing or stale products are scraped.
Run standalone to test:  python product_knowledge.py "Samsung Galaxy S26 Ultra"
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import chromadb
from dotenv import load_dotenv

load_dotenv()

POSTS_TO_FETCH = 30
COMMENTS_PER_POST = 5
# Fetching comments is one API call per post, so only pull them for the most-discussed
# posts (by comment count) to keep us well within RapidAPI's request quota. The other
# posts still contribute their title + body as chunks.
POSTS_WITH_COMMENTS = 8
TOP_K = 10  # chunks handed to the LLM per question

# Reuse previously-scraped Reddit knowledge for this many days before re-scraping. Reddit
# opinions drift over time, so we refresh a product after the TTL rather than caching it
# forever. Override with CACHE_TTL_DAYS in .env.
CACHE_TTL_DAYS = float(os.environ.get('CACHE_TTL_DAYS', '7'))

# Reddit blocks anonymous JSON access and gates its official API behind a builder
# registration, so we source posts through the reddit3 API (SteadyAPI) on RapidAPI.
# Set the RapidAPI key in .env.
RAPIDAPI_KEY = os.environ.get('RAPIDAPI_KEY')
RAPIDAPI_HOST = 'reddit3.p.rapidapi.com'

REQUEST_TIMEOUT = 30
FETCH_WORKERS = 4  # polite parallelism for the comment fetches

# Persistent Chroma client: collections are written to disk so a product we've already
# learned stays in the DB across server restarts (including Flask's debug auto-reload).
# Without this the DB would be empty on every start and we'd re-scrape (and re-spend
# RapidAPI requests) on each product every time. Collections are per-product.
_CHROMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.chroma')
_chroma = chromadb.PersistentClient(path=_CHROMA_DIR)

# Latest RapidAPI quota seen on a response, so the UI can warn before exhaustion.
# The free tier is small (100/month), so we surface it prominently. We persist the
# last-known value to a small file so the meter shows immediately after a restart
# (it self-corrects on the next real API call).
_QUOTA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.quota.json')
_quota = {'remaining': None, 'limit': None}


def _load_quota():
    try:
        with open(_QUOTA_FILE) as f:
            saved = json.load(f)
        _quota['remaining'] = saved.get('remaining')
        _quota['limit'] = saved.get('limit')
    except Exception:
        pass


def _save_quota():
    try:
        with open(_QUOTA_FILE, 'w') as f:
            json.dump(_quota, f)
    except Exception:
        pass


def get_quota():
    """Most recent {remaining, limit} from RapidAPI's rate-limit headers."""
    return dict(_quota)


_load_quota()


def _rapid_get(path, params):
    """GET against the reddit3 (SteadyAPI) RapidAPI host.

    Responses are shaped {meta: {status, cursor, ...}, body: ...}. Returns the
    parsed JSON; callers pull what they need out of `body`. Records remaining quota.
    """
    if not RAPIDAPI_KEY:
        raise RuntimeError(
            'RAPIDAPI_KEY is not set. Subscribe to the reddit3 API on RapidAPI and '
            'add your key to the .env file as RAPIDAPI_KEY=...'
        )
    resp = requests.get(
        f'https://{RAPIDAPI_HOST}{path}',
        params=params,
        headers={'x-rapidapi-host': RAPIDAPI_HOST, 'x-rapidapi-key': RAPIDAPI_KEY},
        timeout=REQUEST_TIMEOUT,
    )

    # Capture quota from headers regardless of status (429s carry them too).
    rem = resp.headers.get('x-ratelimit-requests-remaining')
    lim = resp.headers.get('x-ratelimit-requests-limit')
    if rem is not None:
        _quota['remaining'] = int(rem)
    if lim is not None:
        _quota['limit'] = int(lim)
    if rem is not None or lim is not None:
        _save_quota()

    if resp.status_code == 429:
        raise RuntimeError(
            'RapidAPI monthly request quota exhausted for the reddit3 free plan. '
            'Wait for the monthly reset or upgrade the plan.'
        )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------- scraping

def _clean_query(product_name):
    """Strip spec noise so the Reddit search matches real discussion.

    Product names from the catalog carry parentheticals and storage/color variants
    ("Galaxy S24 Ultra (Titanium Gray, 12GB RAM, 256GB Storage)") that make the search
    far too narrow. We keep the core brand + model that people actually post about.
    """
    name = re.sub(r'\([^)]*\)', '', product_name)   # drop "(Titanium Gray, 256GB...)"
    name = name.split(',')[0]                         # drop trailing ", 256GB Storage"
    name = re.sub(r'\b\d+\s?(GB|TB)\b', '', name, flags=re.I)  # drop "256GB", "12 GB"
    return re.sub(r'\s+', ' ', name).strip() or product_name


def _search_posts(product_name):
    """Return up to POSTS_TO_FETCH post records for the product, best-first.

    One search call returns ~25 posts (plenty), so we don't paginate — every call
    counts against a small monthly quota. Each record is a standard Reddit post dict.
    """
    payload = _rapid_get('/v1/reddit/search', {
        'search': _clean_query(product_name),
        'filter': 'posts',
        'timeFilter': 'all',
        'sortType': 'relevance',
    })
    posts = payload.get('body', []) or []
    return posts[:POSTS_TO_FETCH]


def _fetch_comments(post_url):
    """Top comments for one post. Returns [] on any failure — a missing thread
    shouldn't sink the whole ingestion."""
    try:
        payload = _rapid_get('/v1/reddit/post', {'url': post_url})
        body = payload.get('body', {}) or {}
        return body.get('post_comments', []) or []
    except Exception:
        return []


# ---------------------------------------------------------------- chunking

_EMOJI_ONLY = re.compile(r'^[\W_]+$')

def _is_junk(text, author=''):
    """Rule-based filter for comments/bodies that carry no product signal."""
    if not text:
        return True
    text = text.strip()
    if text in ('[deleted]', '[removed]'):
        return True
    if author in ('AutoModerator', '[deleted]'):
        return True
    if len(text.split()) < 10:           # "this 👆", "lol same", links-only
        return True
    if _EMOJI_ONLY.match(text):
        return True
    return False


def _clip(text, limit=1500):
    """Cap chunk length so one rambling essay doesn't dominate retrieval."""
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def _post_url(post):
    """Full reddit.com URL for a post, from its `url` or `permalink` field."""
    if post.get('url'):
        return post['url']
    return f"https://www.reddit.com{post.get('permalink', '')}"


def build_chunks(product_name, on_progress=None):
    """Scrape Reddit and return a list of {text, metadata} chunks."""
    notify = on_progress or (lambda *_: None)

    notify('searching', 'Searching Reddit discussions...')
    posts = _search_posts(product_name)

    # Only the most-discussed posts get a comment fetch (one API call each); the rest
    # contribute their title + body. Keeps us within the request quota.
    ranked = sorted(posts, key=lambda p: p.get('num_comments', 0), reverse=True)
    with_comments = set(id(p) for p in ranked[:POSTS_WITH_COMMENTS])

    notify('reading', f'Reading {len(posts)} Reddit threads...')
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        comment_trees = list(pool.map(
            lambda p: _fetch_comments(_post_url(p)) if id(p) in with_comments else [],
            posts,
        ))

    chunks = []
    for post, comments in zip(posts, comment_trees):
        url = _post_url(post)
        title = post.get('title', '')

        # The post itself, when it has a real body (reviews, experience threads).
        body = post.get('selftext', '')
        if not _is_junk(body, post.get('author', '')):
            chunks.append({
                'text': _clip(f"{title}. {body}"),
                'metadata': {'source': url, 'type': 'post', 'score': post.get('score', 0)},
            })

        kept = 0
        for c in comments:
            if kept >= COMMENTS_PER_POST:
                break
            # reddit3 puts comment text in `content` (not `body`).
            text = c.get('content') or c.get('body') or ''
            if _is_junk(text, c.get('author', '')):
                continue
            # Prefix the thread title so a bare "battery is great" comment still
            # embeds with its product context.
            chunks.append({
                'text': _clip(f"[{title}] {text}"),
                'metadata': {'source': url, 'type': 'comment', 'score': c.get('score', 0)},
            })
            kept += 1

    return chunks


# ---------------------------------------------------------------- vector store

def _collection_name(product_name):
    slug = re.sub(r'[^a-z0-9]+', '-', product_name.lower()).strip('-')[:50]
    return f'talk-{slug}' or 'talk-product'


def _cache_status(product_name):
    """Whether the vector DB already holds usable knowledge for this product.

    Returns one of:
      'fresh'   - cached and within CACHE_TTL_DAYS  -> reuse it, no scrape needed.
      'stale'   - cached but older than the TTL      -> re-scrape to refresh.
      'missing' - never ingested (or empty)          -> scrape for the first time.
    """
    try:
        collection = _chroma.get_collection(_collection_name(product_name))
    except Exception:
        return 'missing'
    if collection.count() == 0:
        return 'missing'
    ingested_at = (collection.metadata or {}).get('ingested_at', 0)
    age_days = (time.time() - ingested_at) / 86400
    return 'fresh' if age_days <= CACHE_TTL_DAYS else 'stale'


def ingest(product_name, on_progress=None, force=False):
    """Full pipeline: scrape -> chunk -> embed -> store. Returns chunk count.

    If the product is already in the vector DB and still fresh (within CACHE_TTL_DAYS),
    we reuse it and skip scraping entirely — this is what saves RapidAPI requests. Pass
    force=True to re-scrape regardless of what's cached.
    """
    notify = on_progress or (lambda *_: None)

    if not force and _cache_status(product_name) == 'fresh':
        collection = _chroma.get_collection(_collection_name(product_name))
        count = collection.count()
        notify('cached', f'Found {count} saved Reddit opinions — no new scrape needed.')
        return count

    chunks = build_chunks(product_name, on_progress)
    if not chunks:
        raise RuntimeError(
            f'No usable Reddit discussions found for "{product_name}". '
            'Try a more common product name.'
        )

    notify('embedding', f'Organizing {len(chunks)} opinions...')
    name = _collection_name(product_name)
    # Replace any prior (missing/stale) collection with a freshly-scraped one, stamping
    # it with the ingest time so _cache_status can later judge its freshness.
    try:
        _chroma.delete_collection(name)
    except Exception:
        pass
    collection = _chroma.create_collection(name, metadata={'ingested_at': time.time()})
    collection.add(
        ids=[f'c{i}' for i in range(len(chunks))],
        documents=[c['text'] for c in chunks],
        metadatas=[c['metadata'] for c in chunks],
    )
    return len(chunks)


def retrieve(product_name, question, top_k=TOP_K):
    """Return the top_k chunks most relevant to the question:
    [{text, source, score}, ...]. Raises if the product was never ingested."""
    collection = _chroma.get_collection(_collection_name(product_name))
    result = collection.query(query_texts=[question], n_results=top_k)
    docs = result['documents'][0]
    metas = result['metadatas'][0]
    return [
        {'text': d, 'source': m.get('source', ''), 'score': m.get('score', 0)}
        for d, m in zip(docs, metas)
    ]


# ---------------------------------------------------------------- CLI test

if __name__ == '__main__':
    product = ' '.join(sys.argv[1:]) or 'Samsung Galaxy S24 Ultra'
    started = time.time()
    count = ingest(product, on_progress=lambda stage, msg: print(f'[{stage}] {msg}'))
    print(f'\nIngested {count} chunks in {time.time() - started:.1f}s\n')

    for q in ('how is the battery life?', 'is the camera good in low light?'):
        print(f'Q: {q}')
        for hit in retrieve(product, q, top_k=3):
            print(f'  - ({hit["score"]} pts) {hit["text"][:140]}...')
        print()
